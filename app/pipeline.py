"""The correction pipeline: retrieve -> filter -> decide -> apply, with a trace.

Reason codes (every candidate gets exactly one):
    applied                       correction made
    skipped:low-trust             term below the activation threshold
    skipped:vetoed                the user reverted this misspelling (always holds)
    skipped:guarded               learned restraint: context matches sentences
                                  where the user reverted this correction
    skipped:context               LLM judged the ordinary meaning fits
    skipped:conflict              multiple terms matched, no LLM to arbitrate
    skipped:context-unavailable   ambiguous or never-seen spelling, no LLM
    llm-error                     the model call failed; degraded to no-op
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import ACTIVE_TRUST
from .llm import Judge
from .retrieval import Candidate, find_candidates
from .store import Store
from .textutil import (Token, content_words, replace_spans,
                       sentence_initial, tokenize)
from .wordlist import is_commonish


@dataclass
class PipelineResult:
    input: str
    output: str
    asr: str = ""               # raw ASR line, preserved as evidence in the trace
    candidates: list[dict] = field(default_factory=list)
    llm: dict | None = None
    timing_ms: float = 0.0
    reason: str = ""            # summary when nothing was done
    trace_id: int | None = None

    def as_dict(self) -> dict:
        return {"asr": self.asr, "candidates": self.candidates, "llm": self.llm,
                "timing_ms": self.timing_ms, "reason": self.reason}


def _is_ambiguous(cand: Candidate) -> bool:
    """Match strength gates scrutiny.

    - A *fuzzy* match (edit-distance on keys, not key equality) is weak
      evidence: the wordlist cannot be exhaustive, so a fuzzy hit may be an
      ordinary word we simply don't know ("nickel" ~ "Nikhil"). Fuzzy always
      requires contextual judgment.
    - An exact key match is ambiguous only when the misspelling is a single
      common English word ("kiwi"); inflections inherit their base form's
      caution ("kiwis"). Multi-word matches are inherently specific.
    """
    if cand.match == "fuzzy":
        return True
    if cand.match == "join" or cand.word_count > 1:
        # "As if" fusing into the name Asif is ordinary prose; "a ditya" is
        # clearly a split (contains a non-word). A multi-word span made
        # entirely of common words needs context; one containing any
        # non-word token is specific to the personal term.
        return all(is_commonish(w) for w in cand.text.split())
    return is_commonish(cand.text)


def _in_capitalised_phrase(text: str, cand: Candidate) -> bool:
    """True when the span has a capitalised neighbour that is not just the
    sentence's first word - "Adithya Birla", "Aditya Sharma". A name sitting in
    a longer capitalised phrase usually belongs to that phrase.

    Shouted lines count too. "ADITYA BIRLA GROUP" is an organisation whether or
    not the writer was shouting, and an entity name is exactly what upper case
    is used for. The cost is that a shouted instruction ("ASK ADITYA TO REVIEW")
    also needs the judge, which is the cheap direction to be wrong in.

    Digits and a possessive "s" are stepped over rather than read as the
    neighbour: "Aditya 369" and "Aditya's Kitchen" are a film and a restaurant,
    and a guard that only looks one token along walks straight past both.
    """
    toks = tokenize(text)

    def _neighbour(idx: int, step: int) -> Token | None:
        """The next alphabetic, non-possessive token in that direction."""
        j = idx + step
        while 0 <= j < len(toks):
            word = toks[j].text
            if word.isdigit() or word.casefold() == "s":
                j += step           # "369", the "s" of "Aditya's" - keep going
                continue
            return toks[j]
        return None

    for i, t in enumerate(toks):
        if t.start != cand.start:
            continue
        # a bare number straight after the name reads as a title or a version,
        # not as a person: "Aditya 369", "Aditya 2.0"
        if i + 1 < len(toks) and toks[i + 1].text.isdigit():
            return True
        after = _neighbour(i, 1)
        before = _neighbour(i, -1)
        if after is not None and after.text[:1].isupper():
            return True
        return (before is not None and before.text[:1].isupper()
                and not sentence_initial(text, before))
    return False


def _unproven_spelling(text: str, cand: Candidate, trusted: list) -> bool:
    """Whether this spelling is too weakly evidenced to edit without context.

    Shape is judged first, because it can outrank evidence:

    - capitalised inside a longer capitalised phrase ("Aditya Sharma",
      "Adithya Birla Group", "SARVAM AI"): almost certainly a different entity
      that merely shares a first name, so it needs context *however often the
      bare spelling has been corrected before*. Seeing the user fix "aditya"
      on its own says nothing about someone else's surname, and rewriting a
      stranger's name is the worst thing this system can do;
    - lowercase ("dew", "sunny", "ram"): the wordlist cannot vouch that it is
      not ordinary English, so it needs context, unless the user was actually
      seen correcting that spelling. Words that collide with a name this way
      belong in `wordlist.py`, which routes them to the judge every time
      through `_is_ambiguous` - the seen-once waiver never overrides that.
    - capitalised on its own ("Adithya pushed the fix"): a transliteration
      variant of a name the user knows, which is what the phonetic key exists
      to catch. Editable.
    """
    if cand.match != "key" or cand.word_count > 1 or not cand.text.isalpha():
        return False
    if _in_capitalised_phrase(text, cand):
        return True
    cf = cand.text.casefold()
    if any(term.misspellings.get(cf, {}).get("seen", 0) > 0 for term in trusted):
        return False
    return cand.text.islower()


def _match_case(text: str, preferred: str) -> str:
    """Write the preferred spelling in the case the span was written in. An
    all-caps span is a heading or emphasis, not a spelling, so "ADITYA" in a
    shouted line becomes "AADITYA" rather than breaking the line with
    "Aaditya". Only when upper-casing preserves length, so spans stay exact."""
    if len(text) > 1 and text.isupper() and not preferred.isupper():
        upper = preferred.upper()
        if len(upper) == len(preferred):
            return upper
    return preferred


def _guard_decision(cand: Candidate, trusted: list, sentence_words: set[str]) -> str | None:
    """Evaluate learned restraint for this misspelling.

    vetoes with an empty guard context = "never touch this spelling"
    (name-like misspellings). A non-empty context = "hold back only in contexts
    like the ones the user reverted in" - a deterministic skip that spares
    an LLM call once the restraint has been learned.
    """
    misspelling_cf = " ".join(cand.text.casefold().split())
    for term in trusted:
        guard = term.misspellings.get(misspelling_cf, {})
        if guard.get("vetoes", 0) > 0:
            context = guard.get("guard", [])
            if not context:
                return "skipped:vetoed"
            if sentence_words & set(context):
                return "skipped:guarded"
    return None


def run(text: str, store: Store, judge: Judge | None = None,
        record_trace: bool = True, asr: str = "") -> PipelineResult:
    started = time.perf_counter()
    result = PipelineResult(input=text, output=text, asr=asr)

    candidates = find_candidates(text, store)
    if not candidates:
        result.reason = "no-candidates"
        return _finish(result, store, started, record_trace)

    sentence_words = content_words(text)
    replacements: list[tuple[int, int, str]] = []   # applied spans
    llm_items: list[dict] = []                      # deferred to the judge
    entries: list[dict] = []

    for i, cand in enumerate(candidates):
        entry = {"text": cand.text, "span": [cand.start, cand.end],
                 "match": cand.match,
                 "terms": [{"preferred": t.preferred, "kind": t.kind,
                            "note": t.note, "trust": t.trust,
                            "status": t.status} for t in cand.terms]}
        trusted = [t for t in cand.terms if t.trusted]
        guard = _guard_decision(cand, trusted, sentence_words)

        if not trusted:
            entry["decision"] = "skipped:low-trust"
            strongest = max((t.trust for t in cand.terms), default=0)
            entry["reason"] = (f"term below the activation threshold "
                               f"(trust {strongest} < {ACTIVE_TRUST}); "
                               "watching, not acting")
        elif guard:
            entry["decision"] = guard
            if guard == "skipped:guarded":
                entry["reason"] = ("learned restraint: this context matches "
                                   "sentences where the user reverted this correction")
            else:
                entry["reason"] = ("the user previously reverted this exact "
                                   "spelling; it is never auto-corrected again")
        elif (len(trusted) > 1 or _is_ambiguous(cand)
              or _unproven_spelling(text, cand, trusted)):
            # ambiguous: needs context, or stays unchanged
            entry["decision"] = "pending-llm"
            llm_items.append({"index": i, "cand": cand, "trusted": trusted})
        else:
            applied = _match_case(cand.text, trusted[0].preferred)
            replacements.append((cand.start, cand.end, applied))
            entry["decision"] = "applied"
            entry["applied"] = applied
            entry["reason"] = (f"trusted term (trust {trusted[0].trust}); the "
                               "spelling is not a common English word and is "
                               "a proven form of it")
        entries.append(entry)

    if llm_items:
        _judge_batch(text, llm_items, entries, replacements, judge, result)

    # Independent invariant: every replacement must land exactly on a span
    # retrieval produced. The LLM path can therefore never introduce a new
    # span, and a stale/wrong span fails loudly instead of splicing garbage.
    allowed_spans = {(c.start, c.end) for c in candidates}
    for start, end, _ in replacements:
        if (start, end) not in allowed_spans:
            raise RuntimeError(
                f"pipeline invariant violated: replacement at ({start},{end}) "
                "is not a retrieved candidate span")
    result.output = replace_spans(text, replacements)
    result.candidates = entries
    return _finish(result, store, started, record_trace)


def _preferred_elsewhere(text: str, cand: Candidate, terms: list) -> str | None:
    """The preferred spelling of a matched term, if it occurs verbatim as a
    token sequence outside the candidate span."""
    toks = tokenize(text)
    for term in terms:
        want = term.preferred.split()
        for i in range(len(toks) - len(want) + 1):
            window = toks[i:i + len(want)]
            if [t.text for t in window] == want and (
                    window[-1].end <= cand.start or window[0].start >= cand.end):
                return term.preferred
    return None


def _judge_batch(text: str, llm_items: list[dict], entries: list[dict],
                 replacements: list, judge: Judge | None,
                 result: PipelineResult) -> None:
    if judge is None or not judge.available:
        for it in llm_items:
            entries[it["index"]]["decision"] = (
                "skipped:conflict" if len(it["trusted"]) > 1
                else "skipped:context-unavailable")
            entries[it["index"]]["reason"] = "ambiguous; no LLM available, doing nothing"
        return
    request = []
    for it in llm_items:
        item = {"index": it["index"], "text": it["cand"].text,
                "options": [{"preferred": t.preferred, "kind": t.kind,
                             "note": t.note} for t in it["trusted"]]}
        # Deterministic evidence for the judge: when the term's exact
        # preferred spelling already appears elsewhere in this sentence,
        # divergent spelling at this span usually signals a different word
        # ("The Kivi demo crashed while I was eating a kiwi").
        hints = []
        elsewhere = _preferred_elsewhere(text, it["cand"], it["trusted"])
        if elsewhere:
            hints.append(f'the exact spelling "{elsewhere}" already '
                         "appears elsewhere in this sentence")
        if it["cand"].match == "join":
            hints.append("these adjacent words run together sound like the "
                         "term; decide from the clause whether a split name "
                         "or ordinary words was meant")
        if hints:
            item["hint"] = "; ".join(hints)
        request.append(item)
    try:
        verdict = judge.judge(text, request)
    except Exception as exc:        # noqa: BLE001 - degrading is the whole point
        # the shipped Judge raises only LLMError, but "the pipeline never
        # crashes" has to hold for any judge that is plugged in
        for it in llm_items:
            entries[it["index"]]["decision"] = "llm-error"
            entries[it["index"]]["reason"] = str(exc)
        result.llm = {"called": True, "error": str(exc)}
        return

    result.llm = {"called": True, "model": verdict.model,
                  "input_tokens": verdict.input_tokens,
                  "output_tokens": verdict.output_tokens,
                  "cost_usd": verdict.cost_usd}
    for it in llm_items:
        i, cand, trusted = it["index"], it["cand"], it["trusted"]
        decision = verdict.decisions.get(i)
        allowed = {t.preferred for t in trusted}
        # The endpoint guarantees JSON, not our shape - never index blindly.
        apply = decision.get("apply") if isinstance(decision, dict) else None
        preferred = decision.get("preferred") if isinstance(decision, dict) else None
        reason = decision.get("reason", "") if isinstance(decision, dict) else ""
        if apply is True and preferred in allowed:
            applied = _match_case(cand.text, preferred)
            replacements.append((cand.start, cand.end, applied))
            entries[i]["decision"] = "applied"
            entries[i]["applied"] = applied
            entries[i]["reason"] = reason
        elif apply is False:
            entries[i]["decision"] = "skipped:context"
            entries[i]["reason"] = reason
        else:
            entries[i]["decision"] = "llm-error"
            entries[i]["reason"] = "model returned no usable decision for this span"


def _finish(result: PipelineResult, store: Store, started: float,
            record_trace: bool) -> PipelineResult:
    result.timing_ms = round((time.perf_counter() - started) * 1000, 2)
    if record_trace:
        result.trace_id = store.add_trace(result.input, result.output, result.as_dict())
    return result
