# Design - The Words Kivi Keeps

Word-level phonetic memory for a dictation product. It turns **formatted
output** into **memory-aware output**, learns a person's vocabulary from the
edits they already make, and knows when to leave text alone.

---

## 0. The principle everything follows from

**A missed correction is a small annoyance. A wrong correction is not.**

Someone will shrug and fix "aditya" one more time. They will not forgive
"I ate a Kivi for breakfast." Every decision below leans toward doing
nothing, and every correction has to earn its place.

---

## 1. Product decisions

### 1.1 What it means to "know" a word

The system knows a word when the user has **shown it more than once**, not
when it appeared once and not when an algorithm guessed. Trust arrives two
ways:

| Channel  | Signal | Strength |
|----------|--------|----------|
| Explicit | The user adds a term in the dictionary UI | Full trust straight away. They said so. |
| Implicit | The user edits a transcript, and the edit looks like a pronunciation fix | Weak. One sighting makes a watched entry, not knowledge. |

**The implicit gate.** People edit transcripts constantly, and most of those
edits are rewrites ("meeting" to "sync") that say nothing about vocabulary.
Only edits where the old and new word **sound alike** count: "aditya" to
"Aaditya", "kiwi" to "Kivi". A phonetic check on the word-level diff is what
separates learning from noise.

**The homophone trap.** Sound-alike alone would learn grammar fixes, since
"there" and "their" sound identical. So a second gate: if the edit lands on a
common English word it is never learned, unless the user added it explicitly
or it matches the proper-noun pattern, meaning it was capitalised
mid-sentence in otherwise normally-cased text. A title-cased heading or a
word after a colon is formatting, not a name. Grammar stays the formatter's
job.

**Splits and merges.** ASR does not only misspell, it breaks words apart and
runs them together: "Aaditya" becomes "a ditya", "KiviMark" becomes "kivi
mark". So both the diff and retrieval consider joins of adjacent tokens, not
just one-for-one substitutions.

An observation is the triple **(ASR output, formatted output, what the user
kept)**. Corrections are learned from the diff between the last two. The raw
ASR line is stored with the event as evidence of what was actually heard,
since it preserves casing and word boundaries the formatter may have changed.

### 1.2 When it deliberately does nothing

Six product reasons, each with its own code in the trace:

1. `no-candidates` - nothing in the text resembles a known term. The common case.
2. `low-trust` - a watched term matched, but it has only been seen once. Watch, do not act.
3. `context` - "kiwi" matches the term *Kivi*, but the sentence is about fruit.
4. `vetoed` - the user already undid this exact spelling. The veto sticks to
   the spelling and holds even while the term itself stays trusted.
5. `guarded` - learned restraint. The sentence resembles one the user undid
   this correction in, so it is skipped without asking the judge.
6. `conflict` - one spelling maps to two trusted terms, *Aaditya* the
   colleague and *Aditya* the cousin. With a judge available it picks using
   each term's note and the sentence. Without one, it does nothing. Doing
   nothing on real ambiguity is correct behaviour, not a failure.

Two more codes appear when the judge cannot help: `context-unavailable` when
no key is configured, and `llm-error` when the call fails. Both leave the
text unchanged.

---

## 2. Memory model: one entry type, one signed number

No separate object kinds for variants, suppressions or tombstones. The
smallest thing that works:

```
Term:          preferred="Aaditya"  kind=person  note="colleague"  trust=+3 -> trusted
Misspellings:  "aditya"   seen 3x   vetoes=0
               "adithya"  (never seen, still matches through the phonetic key)
```

**Trust sits on the term, distrust sits on the spelling.** If "aditya" to
*Aaditya* has been confirmed three times and ASR then produces "adithya" for
the first time, the term's trust carries over; the new spelling only has to
sound alike. Undoing "kiwi" vetoes that spelling alone, not "kivi".

- A **misspelling** is a spelling ASR tends to produce. The **term** is what
  the user wants written.
- **Trust is one signed counter.** A kept correction or a confirming edit is
  +1. Undoing a correction is -2, because that is the user actively saying it
  was wrong.
- **Confirmations cap at +1 per observation.** Fixing five occurrences in one
  transcript is one sighting, not five, so a single document cannot promote a
  spelling on its own. A revert in the same observation overrides any
  confirmations in it.
- **Status is derived, never stored:**
  - trust >= 2 -> `trusted`, corrections may fire
  - trust = 1 -> `watching`, observed but never acts
  - trust <= 0 -> `muted`, switched off
  - an explicit add starts at the trusted threshold.

  One number instead of a lifecycle state machine, and unlearning falls out
  of it for free.
- **Ambiguity is computed at match time, not stored.** Is the matched text a
  common English word? "kiwi" yes, "aditya" no. A stored flag could never
  cover spellings first seen at match time.
- **One Indic-tuned phonetic key, not Metaphone plus variant generation.**
  The romanisation equivalences (`aa`/`a`, `th`/`t`, `dh`/`d`, `v`/`w`,
  `sh`/`s`, `ee`/`i`, `oo`/`u`, `ph`/`f`, `ksh`/`x`, `z`/`s`, `q`/`k`, among
  others) define one normalising function, so `phonetic_key("aaditya")`,
  `phonetic_key("adithya")` and `phonetic_key("aditya")` are all `"aditya"`,
  and `phonetic_key("kiwi")` is `"kivi"`.
  Retrieval matches on key equality, with bounded edit distance
  as a backstop. Unseen transliterations match with no variant lists to
  maintain, and no English-centric Metaphone. It is a page of rules I can
  explain line by line, tuned to users who dictate Indian names and Hinglish
  all day.
- **Context guards, so restraint is learned and not just counted.** A revert
  is not one-size-fits-all. Undoing "Zeroda" to "Zerodha" means never touch
  that spelling again, because it is name-like and has no ordinary meaning.
  Undoing "kiwi" to "Kivi" in a food sentence means leave it alone in
  sentences like that one, so the sentence's content words become the guard
  condition. A guarded sentence is then a deterministic skip that costs no
  model call, while the term stays correctable elsewhere.
- **Retired terms, so a delete stays deleted.** Deleting a term records its
  phonetic key. The same correction pattern seen later is ignored with reason
  `retired` instead of quietly relearning what the user threw out. Adding the
  term back on purpose lifts it.
- **Observations may carry `presented`**, the text the system actually showed,
  which can include judge-path corrections the deterministic replay cannot
  re-propose. Reverts of those are scored against it span by span. Replay
  stays deterministic because `presented` is recorded in the event rather
  than recomputed.
- **Explicit control.** The user can delete a term, or add it again to fix
  the exact spelling. A `set_trust` event, used by the seed persona and the
  eval, can force a term trusted or muted. Every one of those is an event
  like any other.

### 2.1 Storage and durability

SQLite. The truth is an **append-only `events` table** holding every
observation, revert and explicit change. The `terms` and `misspellings`
tables are a fold over it. That buys three things cheaply:

- `reset` is wipe plus replay of the seed events, so the seed state is
  rebuilt from scratch every time rather than trusted to a fixture file;
- every entry can answer "how do you know this?" by pointing at its events;
- the eval can replay learning over time, exactly.

Tables: `events`, `terms`, `misspellings`, `retired`, `traces`.

**Closing the loop deterministically.** Processing an observation means
running the deterministic pipeline on the formatted text, then comparing with
what the user kept. A correction that survives is a confirm, one they undid
is a revert plus a veto, and a sound-alike edit the system did not make is
new learning. The fold never calls a model, so replay is exactly
reproducible.

---

## 3. The correction pipeline

```
formatted text
     |
     v
[1] RETRIEVE   tokenise, then take n-grams up to the longest stored term and
               joins of adjacent tokens; each is normalised (NFKD, accents
               stripped, casefold) into a phonetic key and matched against the
               index by equality or bounded edit distance. Spans are never
               rewritten, so offsets always point into the original text.
               Overlapping spans resolve longest-first ("sarvam kiwi" beats
               "kiwi"). Spans already written as their preferred spelling are
               dropped, which is what makes the stage idempotent. Spans
               inside an identifier (email, URL, path, handle, variable) are
               dropped too.
     |
     +-- nothing matched ------------> return unchanged. NO MODEL CALL.
     v
[2] FILTER     drop terms below the trust threshold, drop vetoed spellings,
     |         drop guarded contexts. Every drop is logged with its reason.
     +-- none survive ---------------> return unchanged. NO MODEL CALL.
     v
[3] DECIDE     apply directly only when all of these hold: exact key match,
               not a common English word, one matching term, and a proven
               form of the spelling (one the user was seen correcting, one
               containing a digit, or a capitalised form standing alone).
               Everything else goes to the judge: a fuzzy match, a common
               word like "kiwi", a lowercase spelling never seen before, a
               capitalised one inside a longer capitalised phrase, or one
               spelling matching two trusted terms. The judge is handed
               the facts retrieval already knows (the preferred spelling
               appearing elsewhere in the same sentence, or that this was a
               join of split words) and returns decisions only:
               {index, apply, preferred, reason}. It never returns text.
     v
[4] APPLY      my code does the editing. An independent check then asserts
               that every replacement lands exactly on a span retrieval
               produced, so the judge cannot introduce a new one and a stale
               span fails loudly instead of splicing garbage.
     v
memory-aware text  +  decision trace
```

**Idempotency.** Running the pipeline on its own output changes nothing.
Enforced by the preferred-equality drop in stage 1, and tested in the eval,
the unit tests and every fuzz trial.

**Failure policy.** Malformed JSON, a refusal or a timeout degrades that
request to the deterministic result, logged as `llm-error`. The pipeline
never crashes and never guesses. All ambiguous spans in one input go in a
single call, not one call per span.

**The judge works to a deadline.** Dictation is realtime, so waiting is
itself a failure. `KIVI_JUDGE_TIMEOUT` bounds one call, only rate-limit and
transient 5xx replies are retried, and a call that spends its budget is not
given a second one. The default is sized for the free-tier endpoint used here; on a
dedicated endpoint it should be about a second. Worst case for one dictation
is bounded rather than open-ended.

Why this shape:

- **The judge never sees text with no candidates.** Most dictation contains
  no personal terms at all, so most requests cost nothing and add no
  measurable latency. In the committed eval run the model is consulted on
  about a third of inputs, and on none of the sentences that match nothing.
- **The ambiguity test splits the work honestly.** "aditya" is not an English
  word, so replacing it is safe, free and explainable. "kiwi" is a word, and
  only there is contextual judgment worth paying for.
- **The judge is consulted, never trusted.** It returns decisions rather than
  text, so "the model rewrote something else" is not a bug I have to catch;
  there is no channel for it.
- **Phonetic keys and edit distance, not embeddings.** The question is
  "sounds alike", and the key answers exactly that. Embeddings answer "means
  alike", which is the wrong axis, and bring infrastructure the brief warns
  against.
- **Surface forms are handled in matching:** possessives ("aditya's"),
  sentence-initial capitals, and multi-word terms through n-gram windows
  ("sarvam kiwi").

**Every run emits a trace**, stored and returned with the response: the
input, the candidates, the decision and reason for each, the output, latency,
tokens and cost. That trace answers "why did it do that", and doubles as eval
evidence with no extra machinery.

**Degraded mode.** `--no-llm` skips the judge entirely. Ambiguous spans are
left unchanged and logged as `skipped:context-unavailable`. Everything else,
learning, retrieval, deterministic corrections, traces and the eval, runs
with no API key.

---

## 4. Evaluation

### 4.1 Where the data comes from

The brief gives one example and no dataset, so creating the data is part of
the work. Three sources, recorded per case:

1. **Dictated.** Sentences spoken into the live Kivi app, with every real ASR
   mangling kept. The most credible part, because those failures were not
   invented.
2. **Written.** For each category below, cases built from the rules: a name,
   the spellings ASR plausibly produces for it, and both the sentence that
   should be corrected and the one that must not be.
3. **Seed persona.** A fictional user with five terms at different trust
   levels, three trusted, one watching, one muted, giving the demo and the
   eval a reproducible starting state.

The written cases come from the rules rather than from observed output, and
the dictated set keeps its failures. Choosing successful examples after the
fact is exactly what the brief disqualifies, and neither source does that.
Every line is listed in DATA.md.

**Language mix.** Mostly English, because that is the dictation language, but
Indian names with real transliteration variance throughout
(Aditya/Adithya/Aadithya, Lakshmi/Laxmi), plus a small Roman-script Hinglish
subset to show names being corrected while Hindi words are left alone.
Native-script matching is out of scope and stated in the README.

### 4.2 Method

Exact string comparison against expected output. No model judges the results,
so they are reproducible by anyone. Cases live in `eval/cases.jsonl` and each
carries its input, the memory it assumes, the expected output and the
expected reason code. The runner records the actual output and reason beside
them, so a failure is inspectable as input, expected, actual, memory state
and reason.

### 4.3 Case categories, most of which expect nothing to happen

| Category | Example | Passes when |
|---|---|---|
| Clear correction | "ask aditya..." with a trusted term | corrected |
| Common-word collision | "I ate a kiwi for breakfast" | **unchanged**, reason `context` |
| Unlisted English word | "the morning dew" with a friend named Dev | **unchanged**, judged not guessed |
| Inside an identifier | "aditya.kumar@sarvam.ai" | **unchanged**, never a candidate |
| Below threshold | a spelling seen once | **unchanged**, reason `low-trust` |
| Learning over time | observation 1 does nothing, observation 2 corrects | the threshold behaves over time |
| Unlearning | the user undoes a correction | **unchanged** afterwards, reason `vetoed` |
| Edge forms | "aditya's", sentence-initial, "sarvam kiwi service", split "a ditya", "gpt4" | corrected |
| Conflict | the user knows both an Aditya and an Aaditya | judge picks by note, or unchanged with reason `conflict` |
| Homophone grammar fix | the user edits "there" to "their" | **never learned**, and "there" stays untouched later |
| Transliteration variance | "adithya" and "aditya" both reach *Aaditya* | corrected through the shared key |
| Hinglish | "kal aditya ko bolna ki kivi wala demo ready hai" | names corrected, Hindi words untouched |
| Idempotency | the pipeline run on its own output | byte-identical, no model calls |
| Clean text | no personal terms at all | unchanged, no model calls |

### 4.4 Metrics, reported separately and never blended

- **Useful interventions:** how many should-correct cases were corrected.
- **Harmful interventions:** any change on a case that should not change.
  This must be zero.
- **Efficiency:** model call rate, p50 and p95 latency for the deterministic
  and judged paths separately, cost, and database growth per 100
  observations.

One command runs everything and writes results that are committed to the repo.

---

## 5. Deliberately not built

- **No learning from contacts or documents.** Invasive, and appearing in a
  file is weak evidence that someone wants that spelling.
- **No correcting unknown words.** If it is not in memory there is no right
  to touch it, however wrong it looks.
- **No team scope, no episodic or semantic memory, no decay schedule.** Real
  product concerns, wrong scope for the smallest useful thing.
- **No native-script matching.** Entries and transcripts are Roman script.
  Devanagari and Tamil matching is a genuine product need but a different
  assignment. Roman-script Hinglish is supported.
- **No speech recognition.** Waived by the brief; inputs are text.

---

## 6. Demonstration and repo shape

**Primary review method: run it locally**, declared at the top of RUN.md. A
FastAPI backend and one plain HTML page.

Local rather than containerised, deliberately. The core is stdlib-only, so the
only real requirement is a Python interpreter, and a reviewing agent often runs
inside a sandbox where no Docker daemon is available; making a container
mandatory would add a failure mode that has nothing to do with the product.
Two fallbacks ship for machines where the local path is awkward: a
`Dockerfile`, and a `render.yaml` blueprint behind the hosted instance at
https://kivi-memory.onrender.com. Neither is the primary path, because a free
hosted instance sleeps when idle and re-seeds its database on boot, so it can
demonstrate the system but cannot demonstrate memory persisting. What the page
does:

- paste ASR and formatted text, see the memory-aware result with its trace
  rendered beside it;
- file an observation and watch memory change, including clicking a
  correction to undo it;
- browse memory (terms, spellings, trust, status, source; add and delete);
- read the decision traces and the event log the reset replays;
- reset.

Every action has a CLI equivalent for a reviewing agent: `run`, `observe`,
`memory`, `why`, `add`, `delete`, `reset`, with the eval as its own command.

```
kivi-memory/
  README.md          product reasoning, architecture, decisions, limitations, AI use
  RUN.md             how to run it, and what to try
  DESIGN.md          this file
  DATA.md            every line of data and where it came from
  Dockerfile         optional fallback path, not the primary one
  render.yaml        the hosted fallback, defined in code rather than clicks
  .python-version    pins the interpreter for the hosted build
  requirements.txt   FastAPI and uvicorn; the core itself is stdlib only
  .env.example       GROQ_API_KEY=   (optional; the judge is off without it)
  schema.sql         tables and indexes (the versioned migration step is in app/store.py)
  seed/              seed events, replayed to build a reproducible state
  app/               retrieval, learning, pipeline, judge, server, CLI
  tests/             unit tests and the property-based fuzzer
  eval/
    cases.jsonl      the dataset
    run_eval.py      one command, deterministic
    results/         generated output, committed
```

### The risk I am taking

Setting the implicit-learning threshold at 2 costs the user one extra
annoyance per new word, in exchange for near-zero false learning. The
learning-trajectory cases exist to show that trade working. It is a single
constant, so the eval can be run at threshold 1 by editing `ACTIVE_TRUST` in
`app/config.py` to see what breaks.

---

## 7. Compliance check against the brief

| Brief requirement | Where |
|---|---|
| Three transcript versions; moving from 1+2 to 3 | section 1.1 observation triple; section 3 pipeline |
| What remembering means; which observations change behaviour | section 1.1 |
| Handling weak or wrong evidence | section 1.2, section 2 signed trust and vetoes |
| Learning through ordinary use | section 1.1 implicit channel |
| What to store; durability; changing and removing | section 2, section 2.1 |
| Finding relevant memory and placing it into the prompt | section 3 stages 1 to 3 |
| Cases beyond the example; what it distinguishes; where it does nothing | section 1.2, section 4.3 |
| Demo: observations, inspect memory, new inputs, result, why, reset | section 6 |
| Any language, database, model; no speech recognition | section 2.1, section 5 |
| Evaluation: define success, own data, beyond the example | section 4 |
| Preserving inputs, expected, actual, memory state, reason | section 4.2 |
| Useful and unnecessary interventions separated | section 4.4 |
| Latency, model usage, cost, database growth | section 4.4 |
| Reproducible, runnable by a reviewer, inspectable failures | section 4.2, committed results |
| Not cherry-picked after the fact | section 4.1 |
| Repo contents, RUN.md, .env.example, reset procedure | section 6 |
| "The smallest system that has met this person" | section 0, section 2, section 5 |
