"""Learning from ordinary use: process one observation triple.

An observation is (asr, formatted, final): what ASR heard, what the formatter
produced, and what the user actually kept. Processing it:

1. Run our own *deterministic* pipeline on the formatted text (never the LLM,
   so replaying the event log is exactly reproducible).
2. Score each of our applied corrections **span-locally** against the final
   text (never bag-of-words - the same word elsewhere in the sentence must
   not mask a revert):
   - the span still reads exactly as our preferred -> confirm (+1 trust)
   - the span reads as the original misspelling again  -> revert
     (-REVERT_PENALTY + a veto on that misspelling); case-only reverts count
   - the span reads as something else              -> no signal
3. Diff formatted -> final for corrections the user made themselves:
   - the pair must sound alike (phonetic gate: rewrites are not vocabulary)
   - a common-English target is not learned (homophone gate) unless the user
     already made it a term, or it matches the proper-noun pattern:
     capitalized while not sentence-initial ("ask sunny" -> "ask Sunny") in
     normally-cased text - a title-cased line is formatting, not a name
   - 2-token -> 1-token joins are checked too ("a ditya" -> "Aaditya")
4. Confirmations cap at +1 per term per observation; a revert overrides
   them with -REVERT_PENALTY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from . import pipeline
from .config import REVERT_PENALTY
from .phonetics import phrase_key, sounds_alike
from .store import Store, now_iso
from .textutil import Token, content_words, sentence_initial, tokenize
from .wordlist import is_commonish


@dataclass
class ObservationReport:
    learned: list[dict] = field(default_factory=list)
    confirmed: list[dict] = field(default_factory=list)
    reverted: list[dict] = field(default_factory=list)
    ignored: list[dict] = field(default_factory=list)   # edits judged non-vocabulary

    def as_dict(self) -> dict:
        return self.__dict__


def _align(a: list[Token], b: list[Token]) -> tuple[dict[int, int], list]:
    """Casefolded token alignment. Returns (positional a-index -> b-index map
    from equal and same-length replace blocks, the full opcode list).
    Length-changing blocks stay unmapped; `_replaced_block` inspects those."""
    sm = SequenceMatcher(a=[t.text.casefold() for t in a],
                         b=[t.text.casefold() for t in b], autojunk=False)
    ops = sm.get_opcodes()
    mapping: dict[int, int] = {}
    for op, a1, a2, b1, b2 in ops:
        if op == "equal" or (op == "replace" and a2 - a1 == b2 - b1):
            for k in range(a2 - a1):
                mapping[a1 + k] = b1 + k
    return mapping, ops


def _replaced_block(ops: list, b_tokens: list[Token], idxs: list[int]) -> str | None:
    """Text of the b-side replace block that swallowed all a-side `idxs`."""
    lo, hi = idxs[0], idxs[-1]
    for op, a1, a2, b1, b2 in ops:
        if op == "replace" and a1 <= lo and hi < a2:
            return " ".join(t.text for t in b_tokens[b1:b2])
    return None


def _squash(text: str) -> str:
    """Casefolded, whitespace-free form, so a join and its split compare equal."""
    return "".join(text.casefold().split())


def _mostly_capitalized(text: str) -> bool:
    """A heading or title-cased line capitalizes most words; capitalization
    there is formatting, not name evidence. Two shapes: a line whose every
    word is capitalized ("Project Notes", "2024 Release Notes" - digit tokens
    are neutral), or a longer line where at least half the non-initial words
    are ("The Notes from the Meeting"). Deliberate tradeoff: a fully
    capitalized two-word imperative ("Call Priya") reads as a heading too and
    does not learn; a spelling edit or a longer sentence still does."""
    toks = tokenize(text)
    alpha = [t for t in toks if t.text[:1].isalpha()]
    # every alphabetic word capitalized reads as a heading, digit tokens are
    # neutral - "2024 Notes" is a heading even with a single alphabetic word
    if len(toks) >= 2 and alpha and all(t.text[:1].isupper() for t in alpha):
        return True
    non_initial = [t for t in toks if not sentence_initial(text, t)]
    if len(non_initial) < 3:
        return False
    caps = sum(1 for t in non_initial if t.text[:1].isupper())
    return caps * 2 >= len(non_initial)


def _word_pairs(formatted: str, final: str) -> list[tuple[str, str, Token]]:
    """(old, new, new_token) from replace blocks: 1:1 positional + 2->1 joins."""
    ftoks, ntoks = tokenize(formatted), tokenize(final)
    pairs: list[tuple[str, str, Token]] = []
    sm = SequenceMatcher(a=[t.text.casefold() for t in ftoks],
                         b=[t.text.casefold() for t in ntoks], autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            # casefold-equal but case-different ("joy" -> "Joy") is an edit the
            # casefolded alignment cannot see - surface it explicitly. strict=
            # asserts SequenceMatcher's contract that an "equal" block is the
            # same length both sides; a silent zip truncation would drop edits.
            pairs.extend((o.text, n.text, n)
                         for o, n in zip(ftoks[a1:a2], ntoks[b1:b2], strict=True)
                         if o.text != n.text)
            continue
        if op != "replace":
            continue
        olds, news = ftoks[a1:a2], ntoks[b1:b2]
        if len(olds) == len(news):
            pairs.extend((o.text, n.text, n)
                         for o, n in zip(olds, news, strict=True))
        elif len(olds) == 2 and len(news) == 1:     # split fixed by the user
            # the source text, not the two tokens glued together: this string
            # becomes the recorded misspelling, and a veto on "aditya" would
            # silence the term's real correction rather than the split "a ditya"
            pairs.append((formatted[olds[0].start:olds[1].end],
                          news[0].text, news[0]))
    return pairs


def _score_interventions(store: Store, ours: pipeline.PipelineResult, final: str,
                         ts: str, trust_delta: dict[int, int],
                         misspellings_recorded: set, report: ObservationReport) -> None:
    """Span-local confirm/revert scoring of our own applied corrections."""
    applied = [c for c in ours.candidates if c["decision"] == "applied"]
    if not applied:
        return
    out_tokens, fin_tokens = tokenize(ours.output), tokenize(final)
    mapping, ops = _align(out_tokens, fin_tokens)

    # the applied spans, translated from input to output coordinates
    delta = 0
    spans: list[tuple[int, int, dict]] = []
    for entry in sorted(applied, key=lambda c: c["span"][0]):
        start, end = entry["span"]
        out_start = start + delta
        out_end = out_start + len(entry["applied"])
        delta += len(entry["applied"]) - (end - start)
        spans.append((out_start, out_end, entry))

    for out_start, out_end, entry in spans:
        idxs = [i for i, t in enumerate(out_tokens)
                if t.start >= out_start and t.end <= out_end]
        term = store.get_term(entry["applied"])
        if not idxs or term is None:
            continue
        misspelling_cf = " ".join(entry["text"].casefold().split())
        if any(i not in mapping for i in idxs):
            # A length-changing edit swallowed the span - e.g. our join
            # correction "a ditya"->"Aaditya" put back as two tokens. If what
            # the user wrote there is exactly the original misspelling, that
            # is a revert; anything else is a rewrite with no signal.
            block = _replaced_block(ops, fin_tokens, idxs)
            if block is not None and _squash(block) == _squash(misspelling_cf):
                trust_delta[term.id] = -REVERT_PENALTY
                if _record(store, term.id, misspelling_cf, ts, misspellings_recorded,
                           veto=1, guard_words=_guard_words(final, misspelling_cf)):
                    report.reverted.append({"misspelling": entry["text"],
                                            "preferred": term.preferred})
            continue
        final_text = " ".join(fin_tokens[mapping[i]].text for i in idxs)
        if final_text == term.preferred:               # user kept our correction
            if trust_delta.get(term.id, 0) >= 0:
                trust_delta[term.id] = max(trust_delta.get(term.id, 0), 1)
            if _record(store, term.id, misspelling_cf, ts,
                       misspellings_recorded, seen=1):
                report.confirmed.append({"misspelling": entry["text"],
                                         "preferred": term.preferred})
        elif final_text.casefold() == misspelling_cf:      # user put the original back
            trust_delta[term.id] = -REVERT_PENALTY  # overrides any positives
            if _record(store, term.id, misspelling_cf, ts, misspellings_recorded,
                       veto=1, guard_words=_guard_words(final, misspelling_cf)):
                report.reverted.append({"misspelling": entry["text"],
                                        "preferred": term.preferred})


def _record(store: Store, term_id: int, misspelling_cf: str, ts: str,
            recorded: set, seen: int = 0, veto: int = 0,
            guard_words: set[str] | None = None) -> bool:
    """Record one signal once. Returns False when this exact signal was
    already scored in this observation - the deterministic pass and the
    `presented` pass can both see the same revert, and the user should be
    told about it once."""
    key = (term_id, misspelling_cf, "v" if veto else "s")
    if key in recorded:
        return False
    store.record_misspelling(term_id, misspelling_cf, ts, seen_delta=seen,
                             veto_delta=veto, guard_words=guard_words)
    recorded.add(key)
    return True


def _guard_words(sentence: str, misspelling_cf: str) -> set[str]:
    """Context for a learned restraint. A name-like misspelling ("zeroda") gets an
    unconditional guard (empty context: never touch it). An ordinary word
    ("kiwi") gets the sentence's content words, so restraint applies only in
    similar contexts and the term stays correctable elsewhere."""
    parts = misspelling_cf.split()
    if not all(is_commonish(w) for w in parts):
        return set()
    return content_words(sentence, exclude=set(parts))


def _score_presented(store: Store, formatted: str, presented: str, final: str,
                     ts: str, trust_delta: dict[int, int],
                     misspellings_recorded: set, report: ObservationReport) -> None:
    """Score interventions the user actually saw (`presented` = the runtime
    output, including LLM-path corrections the deterministic replay cannot
    re-propose). formatted->presented diffs are our interventions; each is
    checked span-locally against the final text."""
    ptoks, ftoks = tokenize(presented), tokenize(final)
    mapping, ops = _align(ptoks, ftoks)
    starts = {t.start: i for i, t in enumerate(ptoks)}
    for misspelling, preferred, new_token in _word_pairs(formatted, presented):
        term = store.get_term(preferred)
        if term is None or preferred != term.preferred:
            continue
        idx = starts.get(new_token.start)
        if idx is None:
            continue
        misspelling_cf = misspelling.casefold()
        if idx not in mapping:
            # same length-changing revert handling as _score_interventions
            block = _replaced_block(ops, ftoks, [idx])
            if block is not None and _squash(block) == _squash(misspelling_cf):
                trust_delta[term.id] = -REVERT_PENALTY
                if _record(store, term.id, misspelling_cf, ts, misspellings_recorded,
                           veto=1, guard_words=_guard_words(final, misspelling_cf)):
                    report.reverted.append({"misspelling": misspelling,
                                            "preferred": term.preferred})
            continue
        final_text = ftoks[mapping[idx]].text
        if final_text == term.preferred:
            if trust_delta.get(term.id, 0) >= 0:
                trust_delta[term.id] = max(trust_delta.get(term.id, 0), 1)
            if _record(store, term.id, misspelling_cf, ts,
                       misspellings_recorded, seen=1):
                report.confirmed.append({"misspelling": misspelling,
                                         "preferred": term.preferred})
        elif final_text.casefold() == misspelling_cf:
            trust_delta[term.id] = -REVERT_PENALTY
            if _record(store, term.id, misspelling_cf, ts, misspellings_recorded,
                       veto=1, guard_words=_guard_words(final, misspelling_cf)):
                report.reverted.append({"misspelling": misspelling,
                                        "preferred": term.preferred})


def process_observation(store: Store, formatted: str, final: str,
                        ts: str | None = None,
                        presented: str | None = None) -> ObservationReport:
    ts = ts or now_iso()
    report = ObservationReport()
    trust_delta: dict[int, int] = {}
    misspellings_recorded: set = set()

    # -- 1+2: score our interventions, span-locally -------------------------
    ours = pipeline.run(formatted, store, judge=None, record_trace=False)
    _score_interventions(store, ours, final, ts, trust_delta,
                         misspellings_recorded, report)
    if presented and presented != formatted:
        # what the user actually saw, incl. LLM-path corrections the
        # deterministic replay cannot re-propose (dedup via the shared sets)
        _score_presented(store, formatted, presented, final, ts,
                         trust_delta, misspellings_recorded, report)

    # -- 3: learn from the user's own edits (formatted -> final) ------------
    title_cased = _mostly_capitalized(final)
    for old, new, new_token in _word_pairs(formatted, final):
        if old == new:
            continue
        if title_cased and old.casefold() == new.casefold():
            # a title-cased heading recapitalizes everything at once; that is
            # formatting, not vocabulary ("notes" -> "Notes" in a heading)
            report.ignored.append({"old": old, "new": new, "reason": "case-only"})
            continue
        existing = store.get_term(new)
        if existing is None:
            # variant spelling of a word we already know? Credit that term
            # (its preferred wins - the first-learned spelling is kept)
            existing = store.find_term_by_key(phrase_key(new))
        if not sounds_alike(old, new):
            report.ignored.append({"old": old, "new": new, "reason": "not-phonetic"})
            continue
        if existing is None and store.is_retired(phrase_key(new)):
            # the user deleted this term before - it must not creep back in
            # from the same evidence; only an explicit re-add lifts this
            report.ignored.append({"old": old, "new": new, "reason": "retired"})
            continue
        proper_noun = (new[:1].isupper() and old[:1].islower()
                       and not title_cased
                       and not sentence_initial(final, new_token))
        if is_commonish(new) and existing is None and not proper_noun:
            report.ignored.append({"old": old, "new": new, "reason": "common-word"})
            continue
        new_term = existing is None
        if new_term:
            existing = store.upsert_term(new, kind=None, note=None,
                                         trust=0, source="learned", ts=ts)
        cur = trust_delta.get(existing.id, 0)
        if cur >= 0:                                   # a revert always wins
            trust_delta[existing.id] = max(cur, 1)  # cap: +1 per observation
        # an edit that merely kept one of our own corrections was already
        # reported as a confirmation - do not report it as learning too
        if _record(store, existing.id, old.casefold(), ts,
                   misspellings_recorded, seen=1) or new_term:
            report.learned.append({"misspelling": old, "preferred": existing.preferred,
                                   "new_term": new_term})

    for term_id, delta in trust_delta.items():
        if delta:
            store.bump_trust(term_id, delta, ts)
    return report
