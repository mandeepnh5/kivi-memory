# RUN.md

**Primary review method: run it locally.** A SQLite file and a local web
server. It needs no hosted service and no external database, and there is
nothing to install but Python. A hosted instance and a Docker image both
exist as fallbacks, described below and in the appendix; neither is the
primary path.

**If you only want to click something:** https://kivi-memory.onrender.com
It is the same code with a judge key configured. Two caveats, both from the
free tier it runs on. It sleeps after 15 minutes idle, so the first request
can take about a minute to wake it. And its database is re-seeded whenever it
boots, so a term you teach it there disappears when it next sleeps. Running
locally has neither limitation, which is why local is still the primary
method.

## 1. What you need

Python **3.11 or newer**. Nothing else is required: no Docker, no database
server, no build step.

One caveat if you use pyenv or uv: `.python-version` pins 3.12 for the hosted
build, and those tools will apply it the moment you enter the directory. If
you do not have 3.12 installed, every `python` call fails with a version
error that has nothing to do with this project. Delete that file, or run
`pyenv local <a version you have>`; any 3.11+ works.

Verified by running the tests, the evaluation and the fuzzer on Windows
(3.14), Linux x86-64 (3.11, 3.12, 3.13), Linux arm64 / Apple Silicon
architecture (3.13), and Ubuntu 24.04's own system Python (3.12) with no
virtual environment at all.

On macOS and Linux the interpreter is usually called `python3`, not `python`,
and many of them refuse `pip install` outside a virtual environment (PEP 668).
Step 3 creates one, and every command in this file works verbatim once it is
active. If you skip step 3, substitute `python3` for `python` throughout and
the deterministic paths still run; only the web demo needs the two packages.

## 2. Environment variables

All optional. The system, the demo and the deterministic evaluation run with
no environment variables at all. Only the contextual judge needs one:

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | turns on the judge (Groq free tier, `openai/gpt-oss-120b`). `LLM_API_KEY` also works |
| `LLM_BASE_URL` / `LLM_MODEL` | point at any other OpenAI-compatible endpoint, such as a local Ollama |
| `KIVI_JUDGE_TIMEOUT` | seconds the judge may take before the span is left alone (default 15, sized for the free tier; about 1 suits a dedicated endpoint) |
| `KIVI_DB`, `PORT`, `KIVI_HOST` | database path and server bind (defaults `kivi_memory.db`, `8000`, `127.0.0.1`) |

Copy `.env.example` to `.env` and put the key there. The app loads `.env` on
startup, and a real shell variable wins over the file.

Without a key, ambiguous words are left unchanged and logged as
`skipped:context-unavailable`. That is the designed behaviour, not an error.

## 3. Install

Create and activate a virtual environment, then install. This is what makes
`python` mean the right interpreter on every platform, and it is why the
install is not blocked on Linux and macOS.

macOS / Linux:

```
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows (PowerShell):

```
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Keep that shell active for the steps below. Everything except the web demo is
stdlib-only, including the judge, which talks HTTP through urllib. The two
installed packages are the web server.

On a minimal Debian or Ubuntu image the first command reports that `ensurepip`
is unavailable; install `python3-venv` and repeat it, or skip this step
entirely and use `python3` throughout, which runs everything except the web
page.

## 4. Create and seed the database

```
python -m app.cli reset
```

This creates `kivi_memory.db` from `schema.sql` and replays
`seed/seed_events.jsonl` to build the demo memory. Safe to run again at any
time.

On migrations: `schema.sql` is the whole schema and is applied with
`CREATE TABLE IF NOT EXISTS` on every connection. A small versioned step in
`app/store.py` adds columns to databases created under an older schema. A
fresh database and an old one both end up current, with nothing to run by
hand.

## 5. Start it

```
python -m app.cli serve
```

## 6. Open

http://127.0.0.1:8000

## 7. Things to try

Every action exists in the web page and on the command line. The
**Examples** row at the top loads a ready-made sentence for most of these;
pick one and press **Run** (Ctrl+Enter works too).

1. **Correct a transcript.** The prefilled line is the brief's own example.
   Press **Run**, or **Run without judge** if you have no key. Each decision
   and its reason appears under the result.
   CLI: `python -m app.cli run "Ask Aditya to review the Sarvam Kiwi service." --no-llm`

2. **Watch it refuse.** Run `I ate a kiwi for breakfast.` and see it leave
   the fruit alone, with the reason. Then try *both in one line*,
   `The Kiwi demo crashed while I was eating a kiwi.`: with a key, the product
   is corrected and the fruit in the same sentence is not. Without one, both
   are left alone.

3. **Teach it that it was wrong.** After any run that made a correction,
   **click the correction** in the result to put the original word back, which
   is what the user would have done. Press **Submit observation**. The term
   loses trust, that spelling is vetoed, and its status flips to muted. Press
   **Reset** before moving on, so the next steps start from the seed memory.
   CLI: pass `--presented` with what the system showed, and `--final` with the
   text put back:
   `python -m app.cli observe --asr "..." --formatted "Ask Aditya to review." --presented "Ask Aaditya to review." --final "Ask Aditya to review."`

4. **Teach it something new.** In the **Feed an observation** panel, enter
   asr `ask saurabh to check`, formatted `Ask Saurabh to check.`, final
   `Ask Sourabh to check.`, leaving **What the system showed** empty, then
   press **Submit observation**. Do that twice,
   with any two sentences where you fix Saurabh to Sourabh. Now run
   `Ping saurabh now.` The correction only fires after the second
   observation.
   CLI: `python -m app.cli observe --asr ... --formatted ... --final ...`

5. **Look inside memory.** The **Memory** panel on the right, or
   `python -m app.cli memory`. Every term shows its status, trust, the
   spellings seen for it, and any vetoes.

6. **Ask why.** The **Record** panel below Memory has two tabs: the decision
   trace for each run, and the append-only event log that `reset` replays to
   rebuild everything.
   CLI: `python -m app.cli why`

## 8. Run the evaluation

```
python -m eval.run_eval          # no key needed
python -m eval.run_eval --llm    # adds the cases that need the judge
```

Also `python -m tests.test_core` for the unit tests, and
`python -m tests.fuzz` for 300 generated trials (random vocabularies, ASR-style
manglings and junk input, checking the core invariants). Pass `--n` and
`--seed` to push it further; a failure prints the seed that reproduces it.

Each eval run rewrites `eval/results/` in place. Timings and the judge's
wording differ between runs, decisions do not, so `git diff eval/results`
will show those files as modified afterwards. That is expected.

One thing to expect on `--llm`: the free endpoint rate-limits a burst of
calls, and a span whose call comes back 429 is recorded as `llm-error`. The
text is still left alone, which is the behaviour those cases test, but the
reason code is not the one they assert. The summary reports any such case
under **Degraded (the judge endpoint did not answer)** rather than counting it
as a product failure, and a re-run normally clears it. Failures under
`## Failures` are real; entries under `## Degraded` are the endpoint.

What every case is and where it came from: [DATA.md](DATA.md).

## 9. Where results are written

- `eval/results/summary.md` (with the judge) and
  `eval/results/summary-deterministic.md` (without):
  useful and harmful counts kept separate, model call rate, cost, latency and
  storage growth. Also printed to the terminal.
- `eval/results/results.jsonl`, or `results-deterministic.jsonl` when you run
  without the judge: per case, the input, what was expected, what happened,
  the memory state at that moment, and the reason for every decision. Each
  mode writes its own pair, so both runs' evidence survives side by side.

## 10. Reset

```
python -m app.cli reset
```

Wipes everything and replays the seed events. The evaluation needs no reset,
since every case runs in its own fresh in-memory database.

## Appendix. If the local path does not work

### Hosted

https://kivi-memory.onrender.com runs this repository with a judge key set.
`render.yaml` in the repo root defines that service, so the deployment is
reproducible rather than a set of dashboard settings: free plan, build
`pip install -r requirements.txt`, start `python -m app.cli reset && python
-m app.cli serve`, and `KIVI_HOST=0.0.0.0` because the local default binds
loopback. `PORT` is injected by the host and read in `app/cli.py`, so hosting
needed no code change.

Its two free-tier limits are why it is not the primary method. The instance
sleeps after 15 minutes idle, so a first request can take about a minute. And
the start command re-seeds on every boot, so terms taught to it disappear when
it sleeps.

### Docker

Everything above is the primary review method and needs nothing but Python.
Use this only as a fallback, for example if Python 3.11+ is unavailable or the
interpreter refuses `pip install` because it is externally managed.

```
docker build -t kivi-memory .
docker run --rm -p 8000:8000 kivi-memory
```

Then open http://127.0.0.1:8000 as in step 6. The image is built with the seed
persona already applied, so the demo works immediately.

The evaluation and the tests:

```
docker run --rm kivi-memory python -m eval.run_eval
docker run --rm kivi-memory python -m tests.test_core
docker run --rm kivi-memory python -m tests.fuzz
```

To enable the judge, pass the key in rather than baking it into the image
(`.dockerignore` deliberately keeps `.env` out):

```
docker run --rm -e GROQ_API_KEY=... kivi-memory python -m eval.run_eval --llm
```

Results are written inside the container. To read them on the host, mount a
directory over the results folder:

```
docker run --rm -v "$PWD/eval/results:/app/eval/results" kivi-memory python -m eval.run_eval
```

Reset is just a fresh container: the database lives in the container layer, so
`docker run` always starts from the seed state. `python -m app.cli reset`
works inside a running container too.
