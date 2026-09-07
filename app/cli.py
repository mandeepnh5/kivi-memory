"""Command-line interface - every demo interaction, scriptable by a reviewer.

    python -m app.cli reset
    python -m app.cli memory
    python -m app.cli run "Ask Aditya to review the Sarvam Kiwi service."
    python -m app.cli observe --asr "..." --formatted "..." --final "..."
    python -m app.cli why
    python -m app.cli add "Aaditya" --kind person --note "colleague"
    python -m app.cli delete "Aaditya"
    python -m app.cli serve
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Windows consoles default to a legacy codepage; traces can contain
# Devanagari (Kivi's ASR emits it for Hindi speech) and Hinglish.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .config import DEFAULT_DB, _int_env
from .engine import Engine
from .llm import Judge
from .pipeline import run as run_pipeline
from .store import Store, connect


def _engine(args) -> Engine:
    return Engine(Store(connect(args.db)))


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_run(args) -> None:
    engine = _engine(args)
    judge = Judge(enabled=not args.no_llm)
    result = run_pipeline(args.text, engine.store, judge=judge, asr=args.asr)
    print(f"in : {result.input}")
    print(f"out: {result.output}")
    _print(result.as_dict())


def cmd_observe(args) -> None:
    _print(_engine(args).observe(args.asr, args.formatted, args.final,
                                 presented=args.presented))


def cmd_memory(args) -> None:
    for term in _engine(args).store.all_terms():
        misspellings = ", ".join(
            f"{s} (seen {d['seen']}"
            + (f", vetoed {d['vetoes']}x" if d["vetoes"] else "")
            + (f", guarded near: {' '.join(d['guard'])}" if d.get("guard") else "")
            + ")"
            for s, d in term.misspellings.items()) or "-"
        print(f"[{term.status:>9}] {term.preferred}  "
              f"(trust {term.trust:+d}, {term.source}"
              + (f", {term.kind}" if term.kind else "") + ")")
        print(f"            misheard as: {misspellings}")
        if term.note:
            print(f"            note: {term.note}")


def cmd_why(args) -> None:
    traces = _engine(args).store.traces(limit=args.n)
    if not traces:
        print("no traces yet")
        return
    _print(traces if args.n > 1 else traces[0])


def cmd_add(args) -> None:
    result = _engine(args).add_term(args.preferred, args.kind, args.note)
    _print(result)
    sys.exit(1 if "error" in result else 0)


def cmd_delete(args) -> None:
    result = _engine(args).delete_term(args.preferred)
    _print(result)
    sys.exit(0 if result.get("deleted") else 1)


def cmd_reset(args) -> None:
    _print(_engine(args).reset())


def cmd_serve(args) -> None:
    import uvicorn
    from .server import app
    app.state.db_path = args.db
    uvicorn.run(app, host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="kivi-memory")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite file path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="correct a formatted transcript")
    p.add_argument("text")
    p.add_argument("--asr", default="", help="raw ASR line, kept in the trace")
    p.add_argument("--no-llm", action="store_true",
                   help="skip contextual judgment; ambiguous spans stay unchanged")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("observe", help="feed one observation triple")
    p.add_argument("--asr", required=True)
    p.add_argument("--formatted", required=True)
    p.add_argument("--final", required=True)
    p.add_argument("--presented", default=None,
                   help="what the system actually showed (a prior run's output); "
                        "lets reverts of LLM-path corrections be scored")
    p.set_defaults(fn=cmd_observe)

    p = sub.add_parser("memory", help="show memory state")
    p.set_defaults(fn=cmd_memory)

    p = sub.add_parser("why", help="show the latest decision trace(s)")
    p.add_argument("-n", type=int, default=1)
    p.set_defaults(fn=cmd_why)

    p = sub.add_parser("add", help="explicit dictionary add")
    p.add_argument("preferred")
    p.add_argument("--kind")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("delete", help="remove a term")
    p.add_argument("preferred")
    p.set_defaults(fn=cmd_delete)

    p = sub.add_parser("reset", help="wipe and replay the seed persona")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("serve", help="start the web demo")
    # env-overridable bind so a hosted deployment (PORT injected, 0.0.0.0
    # required) needs no code change; local default stays loopback-only
    p.add_argument("--port", type=int, default=_int_env("PORT", 8000))
    p.add_argument("--host", default=os.environ.get("KIVI_HOST", "127.0.0.1"))
    p.set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
