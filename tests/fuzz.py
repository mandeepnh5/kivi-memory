"""Property-based fuzzer: invents random vocabularies, sentences, edits and
garbage, and asserts the system's invariants on every trial.

    python -m tests.fuzz             # 300 trials, fixed seed (reproducible)
    python -m tests.fuzz --n 2000    # more trials
    python -m tests.fuzz --seed 7    # different universe

Invariants checked on every trial (the system's actual promises):
  I1  empty memory       -> any input passes through byte-identical
  I2  changed spans      -> every applied correction maps to a trusted term
  I3  idempotency        -> run(run(x)) == run(x)
  I4  replay determinism -> wipe folded state + replay events == same state
  I5  threshold          -> one implicit sighting never activates a term
  I6  homophone safety   -> a lowercase common-word target is never learned
  I7  crash freedom      -> weird inputs (empty, emoji, non-Latin script, huge)
                            never raise, never change

A failure prints the seed + trial number, so any find is replayable exactly.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ACTIVE_TRUST          # noqa: E402
from app.engine import Engine                    # noqa: E402
from app.pipeline import run                     # noqa: E402
from app.store import Store, connect             # noqa: E402
from app.wordlist import COMMON_WORDS            # noqa: E402

SYLLABLES = ["ka", "ki", "ra", "vi", "na", "dee", "tha", "shu", "pra", "mee",
             "aa", "dha", "ya", "san", "gu", "la", "ram", "esh", "ith", "av",
             "bha", "sri", "nan", "jo", "har", "ul", "twi", "zor", "que", "phi"]
COMMON = sorted(COMMON_WORDS)
# A dictation app really does receive emoji and non-Latin script, so the
# fuzzer has to prove they neither crash nor get edited. This file itself is
# kept to plain ASCII, so the two probes are spelled as escapes: an emoji,
# and the Hindi-script form of "kal milte hain dost".
_EMOJI = "\U0001f95d\U0001f95d\U0001f95d"
_NON_LATIN = ("\u0915\u0932 \u092e\u093f\u0932\u0924\u0947 "
              "\u0939\u0948\u0902 \u0926\u094b\u0938\u094d\u0924")

WEIRD = ["", "   ", "!!! ??? ...", _EMOJI, _NON_LATIN,
         "a", "I I I I I.", "x" * 3000, "It's Bob's dog's toy's tag."]


def fake_word(rng: random.Random) -> str:
    w = "".join(rng.choice(SYLLABLES) for _ in range(rng.randint(2, 4)))
    if rng.random() < 0.2:
        w += rng.choice("245")     # digit-bearing product names ("gpt4")
    return w


def mangle(rng: random.Random, word: str) -> str:
    """A plausible ASR-style corruption of a word."""
    ops = [lambda w: w.replace("aa", "a", 1), lambda w: w.replace("th", "t", 1),
           lambda w: w.replace("v", "w", 1), lambda w: w + w[-1],
           lambda w: w[0] + w[2:] if len(w) > 4 else w, lambda w: w]
    out = rng.choice(ops)(word)
    return out if out != word else (word[:-1] if len(word) > 4 else word + "a")


def sentence(rng: random.Random, extra: list[str]) -> str:
    words = rng.sample(COMMON, rng.randint(3, 7)) + extra
    rng.shuffle(words)
    return (" ".join(words)).capitalize() + "."


def trial(rng: random.Random) -> None:
    engine = Engine(Store(connect(":memory:")))
    store = engine.store

    # I1: empty memory passes everything through
    probe = sentence(rng, [fake_word(rng)])
    assert run(probe, store, record_trace=False).output == probe, "I1"

    # grow a random memory
    preferred_names: list[str] = []
    for _ in range(rng.randint(1, 4)):
        word = fake_word(rng).capitalize()
        if rng.random() < 0.3:
            word += " " + fake_word(rng).capitalize()      # multi-word term
        if word.casefold() in (c.casefold() for c in preferred_names):
            continue
        preferred_names.append(word)
        if rng.random() < 0.5:
            engine.add_term(word, "person", "fuzz")
        else:                                              # implicit: 2 sightings
            misspelling = " ".join(mangle(rng, p) for p in word.lower().split())
            for _ in range(2):
                formatted = sentence(rng, [misspelling])
                engine.observe("", formatted,
                               formatted.replace(misspelling, word))

    # I5: a single fresh sighting never activates
    once = fake_word(rng)
    engine.observe("", f"Ping {once} now.", f"Ping {once.capitalize()}x now.")
    term_once = store.get_term(once.capitalize() + "x")
    assert term_once is None or term_once.trust < ACTIVE_TRUST, "I5"

    # I6: lowercase common-word targets are never learned
    target = rng.choice(COMMON)
    before = store.get_term(target) is not None
    mangled = mangle(rng, target)
    formatted = sentence(rng, [mangled])          # ONE sentence, edited in
    engine.observe("", formatted, formatted.replace(mangled, target))
    assert before or store.get_term(target) is None, f"I6 learned {target!r}"

    # main correction pass over a sentence with mangled known words
    trusted = [t for t in store.all_terms() if t.trusted]
    extras = [" ".join(mangle(rng, p) for p in t.preferred.lower().split())
              for t in rng.sample(trusted, min(2, len(trusted)))] if trusted else []
    text = sentence(rng, extras + [fake_word(rng)])
    result = run(text, store, record_trace=False)

    # I2: anything applied must come from a trusted term
    for cand in result.candidates:
        if cand["decision"] == "applied":
            statuses = [t["status"] for t in cand["terms"]
                        if t["preferred"] == cand["applied"]]
            assert statuses and all(s == "trusted" for s in statuses), "I2"
    if result.output != text:
        assert any(c["decision"] == "applied" for c in result.candidates), "I2b"

    # I3: idempotency on own output
    again = run(result.output, store, record_trace=False)
    assert again.output == result.output, "I3"

    # I4: the folded state is rebuildable from the event log alone. Wipe it
    # first, or a replay that did nothing would satisfy this too.
    snapshot = sorted((t.preferred, t.trust, sorted(t.misspellings))
                      for t in store.all_terms())
    store.clear_state()
    assert store.all_terms() == [], "I4 setup: state not wiped"
    folded = engine.replay()
    replayed = sorted((t.preferred, t.trust, sorted(t.misspellings))
                      for t in store.all_terms())
    assert snapshot == replayed, "I4"
    assert folded == len(store.events()), "I4 event count"

    # I7: garbage never crashes or changes
    weird = rng.choice(WEIRD)
    out = run(weird, store, record_trace=False).output
    known = {p for t in store.all_terms() for p in t.preferred.casefold().split()}
    if not any(w.casefold() in known for w in weird.split()):
        assert out == weird, "I7"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    for i in range(args.n):
        try:
            trial(rng)
        except Exception as exc:
            print(f"FAIL at trial {i} (seed {args.seed}): "
                  f"{type(exc).__name__}: {exc}")
            return 1
    print(f"all {args.n} fuzz trials passed (seed {args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
