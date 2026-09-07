"""Tokenisation with character spans, shared by learning and retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass

# A word is a run of letters or digits ("GPT4" is one token, so digit-bearing
# product names correct cleanly), while a possessive "'s" (straight or curly
# quote) stays out of the base token and "aditya's" corrects to "Aaditya's".
_TOKEN = re.compile(r"[A-Za-z\u00c0-\u024f0-9]+")


@dataclass(frozen=True)
class Token:
    text: str   # as written, without possessive suffix
    start: int  # char offset in the original string
    end: int    # exclusive


def tokenize(text: str) -> list[Token]:
    return [Token(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def words(text: str) -> list[str]:
    return [t.text for t in tokenize(text)]


# tiny function-word set for context-guard extraction - NOT the common-word
# safety list (wordlist.py); this only filters glue words out of guard contexts.
# Time words are here for the same reason as articles: "yesterday" says nothing
# about what a sentence is about, so a guard learned in a food sentence must
# not fire on a software one just because both happened yesterday.
_GLUE = frozenset(
    "the and for was are but not with that this from you your his her its our "
    "has had have will she him they them who what when where why how all any "
    "can could would should there here about into onto out very just also "
    "yesterday today tomorrow tonight morning evening afternoon night day days "
    "week month year time times before after during again now then soon later "
    "already".split())


def sentence_initial(text: str, token: Token) -> bool:
    # colons, semicolons and dashes count as boundaries too: "Subject: Their
    # report" capitalizes "Their" as formatting, which is not name evidence
    i = token.start - 1
    while i >= 0 and text[i] in " \t\"'\u2018\u2019\u201c\u201d(":
        i -= 1
    return i < 0 or text[i] in ".!?:;-\u2013\u2014\n"

def content_words(text: str, exclude: set[str] = frozenset()) -> set[str]:
    """Casefolded content words of a sentence, for guard contexts."""
    return {w for w in (t.text.casefold() for t in tokenize(text))
            if len(w) >= 3 and w not in _GLUE and w not in exclude}


def replace_spans(text: str, replacements: list[tuple[int, int, str]]) -> str:
    """Apply (start, end, new_text) replacements; spans must not overlap."""
    out = []
    pos = 0
    for start, end, new in sorted(replacements):
        if start < pos:
            raise ValueError(f"overlapping replacement at {start}")
        out.append(text[pos:start])
        out.append(new)
        pos = end
    out.append(text[pos:])
    return "".join(out)
