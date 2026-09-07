"""Find spans of text that phonetically match remembered terms.

Deterministic and cheap - this stage decides whether the LLM is consulted at
all, so most inputs (no personal terms) never cost a model call.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import FUZZY_MIN_LEN
from .phonetics import bounded_distance, phonetic_key, phrase_key
from .store import Store, Term
from .textutil import Token, tokenize
from .wordlist import is_commonish

_ARTICLES = frozenset({"a", "an", "the"})

# Characters that mark a span as part of an identifier rather than prose:
# emails, URLs, paths, handles, env vars, filenames. A hyphen is NOT here -
# "Kivi-related" is ordinary hyphenated prose and stays correctable.
_IDENT = frozenset("@/\\_:#$%=&~+")

# Trimmed from a chunk's ends before it is judged, so "Aditya." and "Aditya,"
# read as prose while aditya.kumar@sarvam.ai does not.
_EDGE = ".,;!?\"'\u201c\u201d\u2018\u2019"


def _chunks(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Whitespace-delimited chunks overlapping [start, end). A multi-word span
    can cross a space, so every chunk it touches has to be judged."""
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < len(text) and not text[j].isspace():
            j += 1
        if i < end and start < j:
            out.append((i, j))
        i = j
    return out


def _in_identifier(text: str, start: int, end: int) -> bool:
    """True when the span sits inside an identifier - "aditya" in
    aditya.kumar@sarvam.ai, C:/users/aditya/app.py, KIVI_HOST, @aditya,
    aditya-notes.md. Dictation carries these verbatim and editing one breaks
    it, so they are never candidates.

    What is judged is the whole whitespace-delimited chunk the span sits in,
    not the two neighbouring characters. An identifier can put a hyphen
    between the matched word and the character that gives the chunk away
    ("aditya-kumar@sarvam.ai"), and a one-character lookaround walks straight
    past that. Once sentence punctuation is trimmed from the ends, any
    identifier character or any remaining dot marks the chunk.
    """
    for c_start, c_end in _chunks(text, start, end):
        core = text[c_start:c_end].strip(_EDGE)
        if set(core) & _IDENT or "." in core:
            return True
    return False


@dataclass
class Candidate:
    start: int
    end: int
    text: str                 # matched misspelling as written
    terms: list[Term]         # >1 means a genuine conflict
    match: str                # "key" | "fuzzy" | "join"

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _term_index(terms: list[Term]) -> dict[str, list[Term]]:
    """phonetic key -> terms; keys come from preferred spellings and observed misspellings."""
    index: dict[str, list[Term]] = {}
    for term in terms:
        keys = {phrase_key(term.preferred)}
        keys.update(phrase_key(s) for s in term.misspellings)
        for key in keys:
            if key:
                index.setdefault(key, []).append(term)
    return index


def find_candidates(text: str, store: Store) -> list[Candidate]:
    terms = store.all_terms()
    if not terms:
        return []
    index = _term_index(terms)
    tokens = tokenize(text)
    max_words = max(len(t.preferred.split()) for t in terms)

    found: list[Candidate] = []

    def contiguous(window: list[Token]) -> bool:
        """Exactly one space may separate the window's tokens. Punctuation is
        excluded because a multi-word term must not match across it
        ("Gajendra, circle"), and anything wider than a single space -- a
        newline, a tab, a double space -- is excluded because the replacement
        is written with single spaces and would silently reflow the text,
        merging two lines of a dictated list into one."""
        return all(text[a.end:b.start] == " " for a, b in zip(window, window[1:]))

    # n-gram windows up to the longest stored term, longest first
    for n in range(max_words, 0, -1):
        for i in range(len(tokens) - n + 1):
            window = tokens[i:i + n]
            if not contiguous(window):
                continue
            misspelling = text[window[0].start:window[-1].end]
            key = phrase_key(misspelling)
            matched = index.get(key)
            kind = "key"
            if not matched and n == 1 and len(key) >= FUZZY_MIN_LEN:
                fuzz = [t for k, ts in index.items() if " " not in k
                        and bounded_distance(key, k, 1) <= 1 for t in ts]
                if fuzz:
                    matched, kind = _dedupe(fuzz), "fuzzy"
            if matched and not _in_identifier(text, window[0].start, window[-1].end):
                found.append(Candidate(window[0].start, window[-1].end,
                                       misspelling, _dedupe(matched), kind))

    # split-error joins: "a ditya" -> key("aditya")
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        if not contiguous([a, b]):
            continue
        if a.text.casefold() in _ARTICLES and is_commonish(b.text):
            continue   # "a man" is prose, never a split "Aman"; "a ditya" still joins
        joined = index.get(phonetic_key(a.text + b.text))
        if joined and not _in_identifier(text, a.start, b.end):
            found.append(Candidate(a.start, b.end, text[a.start:b.end],
                                   _dedupe(joined), "join"))

    return _resolve(found)


def _dedupe(terms: list[Term]) -> list[Term]:
    seen: dict[int, Term] = {}
    for t in terms:
        seen.setdefault(t.id, t)
    return list(seen.values())


def _resolve(found: list[Candidate]) -> list[Candidate]:
    """Longest-match-first, non-overlapping; drop spans already preferred."""
    kept: list[Candidate] = []
    claimed: list[Candidate] = []   # kept spans PLUS already-correct ones
    for cand in sorted(found, key=lambda c: (-(c.end - c.start), c.start)):
        if any(c.start < cand.end and cand.start < c.end for c in claimed):
            continue
        # A longer span wins its text even when the answer is "leave it
        # alone": claiming it stops a shorter match from editing inside an
        # already-correct name. Without this, a stored "Aaditya Kumar" is
        # dropped here and a stored "Aadityaa" then rewrites its first word,
        # so the pipeline is not idempotent and oscillates between the two.
        claimed.append(cand)
        # Idempotency: text already written exactly as one of its matched
        # preferred spellings is not a candidate. Possessives fall out of
        # this too, since "'s" is never part of the token.
        if any(cand.text == t.preferred for t in cand.terms):
            continue
        kept.append(cand)
    return sorted(kept, key=lambda c: c.start)
