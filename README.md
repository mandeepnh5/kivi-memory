# The Words Kivi Keeps - word-level phonetic memory

This is the layer that turns *formatted output* into *memory-aware output*.
It learns the words a person actually uses, from the edits they already make,
and it knows when to leave text alone.

```
ASR:          ask aditya to review the sarvam kiwi service
Formatted:    Ask Aditya to review the Sarvam Kiwi service.
Memory-aware: Ask Aaditya to review the Sarvam Kivi service.   <- this system
```

Only the second edit needs a key. "Aditya" is not an English word, so it is
corrected deterministically; "Kiwi" is one, so it is left alone unless the
judge confirms the product is meant. Without a key that second edit is
deliberately skipped, not failed.

**To run it:** [RUN.md](RUN.md). **Why it is built this way:** [DESIGN.md](DESIGN.md).
**Where the data came from:** [DATA.md](DATA.md).
**Hosted demo:** https://kivi-memory.onrender.com (free tier, so give it a
minute to wake; running locally is the primary method).

## The decision everything else follows from

A missed correction is a small annoyance. A wrong correction is not. Someone
will shrug and fix "aditya" one more time, but they will not forgive
"I ate a Kivi for breakfast."

So I built the system to prefer doing nothing, and made every correction earn
its place. Doing nothing is a real outcome here, not a failure: each one is
logged with a reason you can read - `no-candidates`, `low-trust`, `context`,
`vetoed`, `guarded` and `conflict`, plus `context-unavailable` and `llm-error`
when the judge is missing or fails.

## How it works

```
                   observation (asr, formatted, what the user kept)
                                        |
                                   [learning]
              only edits that sound alike count as vocabulary
              common English words are never learned
                                        |
   events (append-only) --fold--> terms + misspellings   (SQLite)
                                        |
 formatted text -> [retrieve] -> [filter] -> [decide] -> [apply] -> memory-aware
                 phonetic key    trust      judge only    my code
                 index, no model threshold, for genuine   does the
                 call if nothing vetoes     ambiguity     editing
                 matches
```

**One kind of memory entry.** A *term* holds the spelling the user wants, an
optional kind and note, and one signed number: trust. A confirmation adds 1
(at most 1 per dictation), a revert subtracts 2. The status falls out of that
number: 2 or more is `trusted` and may correct, 1 is `watching` and never
acts, 0 or less is `muted`. The spellings ASR produces are stored against the
term, and each one carries its own veto count. Trust sits on the term,
distrust sits on the spelling.

**It learns from ordinary editing.** When someone fixes their own transcript,
I compare what the formatter wrote with what they kept. Only sound-alike
edits count as vocabulary, so "meeting" changed to "sync" teaches nothing.
Edits that land on a common English word are treated as grammar rather than
vocabulary, so "thier" to "their" is never learned. The exception is a name
that is also a word: capitalising "joy" to "Joy" mid-sentence is how English
marks a name, so that is learnable, while a title-cased heading is not. One
sighting starts watching. The second makes it act.

**Restraint is learned too.** Undoing a correction costs the term 2 trust and
leaves a guard on that spelling. For a name-like spelling the guard is
absolute: "Zeroda" is never touched again. For an ordinary word the guard
remembers the sentence it happened in, so "kiwi" stays untouched in food
sentences and stays correctable when the product is meant. Deleting a term
retires it, so the same evidence cannot quietly bring it back; adding it
again on purpose lifts that.

**Phonetics, not embeddings.** One key function collapses the romanisation
variants Indian names actually take (`aa` and `a`, `th` and `t`, `v` and `w`,
`sh` and `s`, `ksh` and `x`, `ee` and `i`), so `phonetic_key("Aaditya")`,
`phonetic_key("adithya")` and `phonetic_key("aditya")` are the same string.
A spelling nobody has seen before still matches, with no variant lists to
maintain. Bounded
edit distance on those keys is the backstop for ordinary typos. Embeddings
measure "means alike", which is the wrong question here.

**The judge is consulted, never trusted.** Only genuinely ambiguous spans
reach the model: a common English word like "kiwi", a spelling never seen
before, a fuzzy match, or one spelling matching two people. They go in a
single call, and the model returns decisions, not text. My code does the
editing, so the model has no way to touch anything outside the spans
retrieval already found. If the call fails, that request falls back to the
deterministic result. With no API key at all the system still works and
simply leaves ambiguous spans alone.

**A separate stage, not a bigger formatting prompt.** The brief sketches
memory being placed into the formatting prompt. I run after formatting
instead, and put retrieved terms into my own judge prompt only when a span is
ambiguous. Four reasons. The memory step can then be measured on its own,
which production cannot do because it folds this work into `FormattingMs`.
Most sentences match nothing, so they never reach a model rather than making
every formatting prompt larger. A wrong word is traceably mine or the
formatter's, never a blend. And the layer stays idempotent over text the
formatter already fixed, which my harvested data shows really happens.

**Event-sourced storage.** Every change is an append-only event, and the
terms and misspellings tables are a fold over that log. The fold never calls
a model, so replay is exact. Reset is wipe plus replay. Every entry can
answer "how do you know this?" by pointing at the events behind it.

## Evaluation

The dataset is 63 cases. `python -m eval.run_eval` runs the 54 that need no
key and skips the other 9; `--llm` runs all 63. Each case gets a fresh
in-memory database, and results are compared as exact strings, so no model
judges the outcome.

**48 of the 66 scored runs expect nothing to change.** That is the point of
the dataset. One of the 47, `hard-05`, accepts either outcome and so cannot
fail in either direction, and so does `guard-02`; the other 46 are strict.
Those cases cover
fruit-kiwi sentences, plurals, terms seen only once, spellings the user
already rejected, grammar fixes, Hinglish filler, clean text, words inside
email addresses and file paths, and running the pipeline on its own output.

Useful and harmful interventions are counted separately and never blended:

| Mode | Cases | Useful | Harmful | Model calls |
|---|---|---|---|---|
| deterministic | 54/54 | 12/12 | **0** | none, p95 under 1 ms |
| with `--llm` | 63/63 | 18/18 | **0** across 48 traps | about a third of runs, $0.00 |

Several cases exist because something was actually broken and got fixed:
span-local revert detection, "As if" being read as the name Asif,
digit-bearing names, title-cased headings, undone join corrections, ordinary
English words colliding with names, terms edited inside emails and file
paths, a stranger's name rewritten because it shared a first name with a
known term, and the judge rewriting "eating a kiwi" because the product appeared
elsewhere in the same sentence. The `join-guard`, `proper-noun-learning`,
`formatting`, `edge-forms`, `trap-and-term`, `unlisted-word`, `identifier`,
`shared-first-name` and `unlearning` cases hold those fixes in place. Full output is in
`eval/results/`.

`python -m tests.fuzz` adds a layer a hand-written eval cannot: it invents
random vocabularies, mangles them the way ASR does, and throws junk at the
system, checking the same promises every time. Only trusted terms may
correct. Common words are never learned. Empty memory changes nothing.
Running twice equals running once. Replay rebuilds the same state. Nothing
crashes. It is seeded, so any failure prints the seed that reproduces it.
I wrote both the code and the eval, so random input is the only part neither
of them shaped.

**Where the data came from.** 52 cases written by category, and 11 harvested
from real dictation into the Kivi Windows app, with the raw ASR and formatted
pairs kept in `eval/harvested_raw.json`. The harvested set keeps its
failures: "Adithya" came back as "Itya", "Moodle" as "nodal", "quiz" as
"cruise". No sound-based system recovers those, so the eval asserts it leaves
them alone. Two things that harvest taught me, which the design now assumes:
Kivi's ASR returns Devanagari for Hindi speech and the formatter
transliterates it, and the production dictionary had already fixed "Kiwi" to
"Kivi" in some outputs, which is why running on already-correct text has to
be a no-op. Every line is listed in [DATA.md](DATA.md).

## Latency

Dictation is realtime, so the design treats the model call as the expensive
thing and arranges for most sentences never to reach it:

- a sentence matching nothing returns before any model call, which is most
  dictation;
- every ambiguous span in one sentence goes in a single call, not one per span;
- once a guard is learned, that restraint becomes a deterministic skip and
  stops costing a call at all.

Measured in the committed eval run:

| Path | p50 | p95 | Share of runs |
|---|---|---|---|
| deterministic | ~0.5 ms | under 1 ms | about two thirds |
| judged | ~1.1 s | ~1.9 s | about one third |

Exact figures for the committed run are in `eval/results/summary.md`, and a
rerun will differ a lot on the judged row: those numbers describe a free
shared endpoint, not this code. Its median stays close to a second, but I have
measured the p95 anywhere from two to eight seconds depending on how busy the
endpoint is, which is why the deadline below exists.

**The judge has a deadline.** A call that does not answer in time is treated
as "leave the span alone", because a person waiting for text cannot wait for
a retry loop. `KIVI_JUDGE_TIMEOUT` sets it, only rate-limit and transient 5xx
replies are retried, and the worst case for one dictation is bounded at about 32 seconds
rather than the couple of minutes an unbounded retry loop would allow. The
default of 15 seconds is sized for the free tier used here; on a dedicated
endpoint the right value is about 1 second.

**What I would change first under load.** Retrieval rebuilds the phonetic
index from memory on every request. Measured ad hoc, not in the committed run,
that is 0.04 ms at 5 terms, 0.3 ms at 50, and 4 ms at 500, where it becomes
about a third of the whole run.
Personal vocabularies are small, so it is free at demo scale, but at a few
hundred terms per user it is the first thing worth caching, invalidated when
memory changes. I have not built that, because there is no measured problem
yet and a cache would add invalidation logic to the smallest part of the
system.

## What it deliberately does not do

- **No learning from contacts or documents.** It is invasive, and appearing
  in a file is weak evidence that someone wants that spelling.
- **No correcting words that are not in memory**, however wrong they look.
- **No editing inside identifiers.** A word in an email address, URL, file
  path, handle, filename or environment variable is left exactly as dictated.
  The whole whitespace-delimited chunk is judged, not the two characters
  either side of the word, so `aditya-kumar@sarvam.ai` is as safe as
  `aditya.kumar@sarvam.ai`.
- **No team scope, no episodic or semantic memory, no decay schedule.**
- **No native-script matching.** Roman-script Hinglish works; matching inside
  Devanagari or Tamil is a different assignment.
- **No speech recognition.** Inputs are text, as the brief says.

## Known limitations

- **The common-word list is curated (~1000 words), not exhaustive**, so I do
  not let it be the only thing standing between a trusted term and an edit.
  A deterministic correction needs a spelling the system has actually seen
  the user fix, or one containing a digit, or a capitalised form standing on
  its own. Everything else goes to the judge. A lowercase spelling it has
  never seen may be ordinary English the list lacks, so a friend named Dev
  does not turn "dew" into "Dev" (case noop-13). A never-seen capitalised
  spelling inside a longer capitalised phrase is more likely a different
  entity that merely sounds alike, so "Adithya Birla Group" is left alone
  while "Adithya pushed the fix" is still corrected. Fuzzy matches always
  need judgment too ("nickel" against "Nikhil", case noop-09). The cost is
  missing the first correction of a brand-new spelling; one edit from the
  user proves it, and it works from then on.
- **The phonetic key is spelling-based.** True homophones spelled differently
  ("there" and "their") get different keys. For learning that is covered by
  the common-word rule; for retrieval it means a term is only found if it
  sounds alike under the key rules or is within one edit.
- **The judged path is only as good as the model behind it.** One case,
  `hard-05`, splits "kiwi mark" back into KiviMark and rests entirely on the
  judge, because both halves are ordinary words. The free-tier model gets it
  right in most runs but not all, so that case accepts either outcome. One
  other, `guard-02`, asks the judge to correct "the kiwi rollout" after a
  guard was learned in a food sentence; under the load of a full judged run
  the free endpoint declines that in roughly one run in seven, so it accepts
  either outcome too. The mechanism it exists to prove - that a conditional
  guard leaves other contexts open - is pinned deterministically by
  `guard-01`, which needs no key and asserts the product sentence reaches
  `context-unavailable` rather than `guarded`. In both cases the failure mode
  is a missed correction, never a wrong one.
- **Alternating spellings keep the first one learned.** If someone writes
  both "Adithya" and "Aaditya" for the same person, the first stays; changing
  it is a deliberate delete and re-add.
- **Term spellings are letters and digits only.** "GPT4" is fine; "GPT-4" and
  "A/B" are rejected when added, because the tokenizer cannot round-trip them
  and every run would splice the stray characters back in.

## AI use

The design decisions here are mine: the trust model and its thresholds,
learning from the user's own edits instead of scraping their contacts, the
restraint-first policy and its named reasons, having the judge return
decisions rather than text, and the things I chose not to build. Each one is
argued in DESIGN.md. The harvested eval data came from me dictating into the
real Kivi app.

I used Claude heavily for the implementation: turning the design into code,
drafting tests and documentation, and arguing with my choices. Several of the
bugs listed above surfaced in that back and forth, and each one became an
eval case. None of this requires taking my word for it. The unit tests, the
62-case eval and the fuzzer all run on your machine.

Separately, the running system uses a small model at exactly one point, the
contextual judge. It talks to one OpenAI-compatible endpoint over plain HTTP,
Groq's free tier and `openai/gpt-oss-120b` by default, which is what every
number here was measured against. Without a key those spans are left
unchanged.
