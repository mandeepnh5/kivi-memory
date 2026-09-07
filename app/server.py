"""FastAPI backend for the web demo. All logic lives in the core modules;
this file only maps HTTP onto the engine.

Write endpoints share one SQLite connection, serialised by a lock (SQLite
writes are single-writer anyway, and this keeps event append + fold atomic
per request). /api/run instead opens a per-request connection, because a run
may sit in the judge's network call for a while and must not hold the lock
that every other endpoint needs; WAL makes the concurrent read safe.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import DEFAULT_DB
from .engine import Engine
from .llm import DEFAULT_MODEL, Judge
from .pipeline import run as run_pipeline
from .store import Store, connect

app = FastAPI(title="Kivi word memory")
app.state.db_path = DEFAULT_DB

_INDEX = Path(__file__).resolve().parent / "static" / "index.html"
_lock = threading.Lock()
_engine: Engine | None = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(Store(connect(app.state.db_path)))
    return _engine


class RunBody(BaseModel):
    formatted: str
    asr: str = ""
    no_llm: bool = False


class ObserveBody(BaseModel):
    asr: str
    formatted: str
    final: str
    presented: str | None = None   # what the system actually showed (optional)


class TermBody(BaseModel):
    preferred: str
    kind: str | None = None
    note: str | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _INDEX.read_text(encoding="utf-8")


@app.post("/api/run")
def api_run(body: RunBody) -> dict:
    judge = Judge(enabled=not body.no_llm)
    if app.state.db_path == ":memory:":
        # in-memory DBs exist per connection; tests share the locked one
        with _lock:
            result = run_pipeline(body.formatted, _get_engine().store,
                                  judge=judge, asr=body.asr)
    else:
        # A run may include the judge's network call; holding the shared
        # lock through it would freeze every other endpoint. Use a
        # per-request connection instead: WAL lets the read run beside
        # writers and busy_timeout covers the single trace write.
        store = Store(connect(app.state.db_path))
        try:
            result = run_pipeline(body.formatted, store, judge=judge,
                                  asr=body.asr)
        finally:
            store.conn.close()
    return {"input": result.input, "output": result.output,
            "trace_id": result.trace_id, **result.as_dict()}


@app.post("/api/observe")
def api_observe(body: ObserveBody) -> dict:
    with _lock:
        return _get_engine().observe(body.asr, body.formatted, body.final,
                                     presented=body.presented)


@app.get("/api/status")
def api_status() -> dict:
    """Whether the judge is configured, so the page can say up front why an
    ambiguous word was left alone rather than leaving a reviewer guessing."""
    judge = Judge()
    return {"judge": judge.available,
            "model": DEFAULT_MODEL if judge.available else None}


@app.get("/api/memory")
def api_memory() -> list[dict]:
    with _lock:
        return [t.as_dict() for t in _get_engine().store.all_terms()]


@app.post("/api/terms")
def api_add_term(body: TermBody) -> dict:
    with _lock:
        result = _get_engine().add_term(body.preferred, body.kind, body.note)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.delete("/api/terms/{preferred:path}")  # :path so names with "/" still route
def api_delete_term(preferred: str) -> dict:
    with _lock:
        result = _get_engine().delete_term(preferred)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail=f"no such term: {preferred}")
    return result


@app.get("/api/traces")
def api_traces(limit: int = 10) -> list[dict]:
    # clamped: the query string is user input, and a value past SQLite's
    # 64-bit range would surface as a 500 rather than a page of traces
    limit = max(1, min(limit, 200))
    with _lock:
        return _get_engine().store.traces(limit=limit)


@app.get("/api/events")
def api_events() -> list[dict]:
    with _lock:
        return [dict(r) for r in _get_engine().store.events()]


@app.post("/api/reset")
def api_reset() -> dict:
    with _lock:
        return _get_engine().reset()
