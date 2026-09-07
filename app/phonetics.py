"""Phonetic normalisation tuned for romanized Indian names and English.

One mechanism does all "sounds alike" work in the system: `phonetic_key`
collapses the standard romanization equivalences (aa/a, th/t, v/w, ...) so
that every transliteration variant of a name maps to the same key.

    phonetic_key("Aaditya") == phonetic_key("adithya") == "aditya"
    phonetic_key("kiwi")    == phonetic_key("Kivi")    == "kivi"

A bounded Damerau-Levenshtein distance on keys is the backstop for residual
typo-level differences the rules don't cover.
"""

from __future__ import annotations

import re
import unicodedata

# Ordered: longer digraphs first so e.g. "ksh" wins over "sh"/"kh".
_EQUIVALENCES: list[tuple[str, str]] = [
    ("ksh", "x"),
    ("ph", "f"),
    ("th", "t"),
    ("dh", "d"),
    ("bh", "b"),
    ("gh", "g"),
    ("kh", "k"),
    ("jh", "j"),
    ("zh", "j"),
    ("sh", "s"),
    ("ch", "c"),
    ("ck", "k"),
    ("ee", "i"),
    ("oo", "u"),
    ("ou", "au"),
    ("w", "v"),
    ("z", "s"),
    ("q", "k"),
    ("c", "k"),  # after ch/ck have been consumed
]

# Digits survive into the key: "gpt4" and "gpt" must NOT collapse together,
# or a correction to "GPT4" would splice over the bare "gpt" and duplicate
# the digit, while "sector 7" needs the 7 to anchor its second word.
_NON_KEY = re.compile(r"[^a-z0-9]+")


def phonetic_key(word: str) -> str:
    """Collapse a single word to its phonetic equivalence-class key."""
    w = unicodedata.normalize("NFKD", word)
    w = "".join(ch for ch in w if not unicodedata.combining(ch))  # strip accents
    w = _NON_KEY.sub("", w.casefold())
    for src, dst in _EQUIVALENCES:
        w = w.replace(src, dst)
    # Collapse runs of the same LETTER: "aaditya" -> "aditya", "anna" -> "ana".
    # Digits never collapse - "22" does not sound like "2", and folding them
    # would rewrite quantities ("room 2" must never become "Room 22").
    return re.sub(r"([a-z])\1+", r"\1", w)


def phrase_key(text: str) -> str:
    """Key for multi-word text: per-word keys joined by a space."""
    keys = (phonetic_key(p) for p in text.split())
    return " ".join(k for k in keys if k)


def bounded_distance(a: str, b: str, limit: int) -> int:
    """Damerau-Levenshtein distance, early-exiting above `limit`.

    Returns limit + 1 when the true distance exceeds `limit`.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + cost)  # transposition
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[len(b)]


def sounds_alike(a: str, b: str) -> bool:
    """True when two single words belong to the same phonetic neighbourhood."""
    from .config import FUZZY_MIN_LEN  # same threshold retrieval uses

    ka, kb = phonetic_key(a), phonetic_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # Backstop: allow 1 residual edit on the keys, but only for words long
    # enough that a single edit is unlikely to bridge two unrelated words.
    if min(len(ka), len(kb)) >= FUZZY_MIN_LEN:
        return bounded_distance(ka, kb, 1) <= 1
    return False
