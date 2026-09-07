"""Tunables in one place. Every number here is a product decision (DESIGN.md)."""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (stdlib only): KEY=VALUE lines, # comments.
    Real environment variables always win over the file."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)


_load_dotenv()


def _int_env(name: str, default: int) -> int:
    """Read an int from the environment, ignoring anything unparseable. A
    host that injects an empty or malformed PORT must not break commands that
    have nothing to do with serving."""
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    """Read a float from the environment, ignoring anything unparseable.
    A stray value must not break startup: the deterministic path is
    documented to need no environment at all."""
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default

# A term may fire corrections once its net trust reaches this.
# Implicit learning grants +1 per observation, so a word must be seen twice;
# explicit dictionary adds start at the threshold (the user told us).
ACTIVE_TRUST = 2

# A revert (the user undoing our correction) is the user explicitly saying
# we were wrong - it outweighs a routine confirmation.
REVERT_PENALTY = 2

# Fuzzy key matching only for words at least this long: a single edit can
# bridge two unrelated short words, but rarely two unrelated long ones.
FUZZY_MIN_LEN = 4

# How long one dictation may wait on the judge: a product budget, not a
# network setting. Someone is waiting for text, so a call that does not answer
# in time becomes "leave the span alone". About a second suits a dedicated
# endpoint; the default suits the free tier used here, whose p95 has been
# measured anywhere from two to eight seconds depending on how busy it is.
JUDGE_TIMEOUT_S = _float_env("KIVI_JUDGE_TIMEOUT", 15.0)
JUDGE_ATTEMPTS = 2
JUDGE_BACKOFF_S = 2.0

# Without this key the system still works; ambiguous spans are left alone.
JUDGE_KEY_VAR = "GROQ_API_KEY"

# USD per million tokens (input, output). An endpoint with no entry reports 0
# rather than an invented number; add a row when pointing at a paid one.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.0, 0.0),   # Groq free tier
}

DEFAULT_DB = os.environ.get("KIVI_DB", "kivi_memory.db")
