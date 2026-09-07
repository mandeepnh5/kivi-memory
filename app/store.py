"""State access: events, terms, misspellings, traces. No learning logic here."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import ACTIVE_TRUST

_SCHEMA = (Path(__file__).resolve().parent.parent / "schema.sql").read_text(encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cf(text: str) -> str:
    """The canonical lookup key: NFC-normalised, then casefolded. Writes and
    reads must agree on it. Some IMEs emit a decomposed accent, so a name
    typed with a combining mark has to find the term stored in precomposed
    form - otherwise it adds once and can never be found or deleted."""
    return unicodedata.normalize("NFC", text).casefold()


def connect(db_path: str) -> sqlite3.Connection:
    # check_same_thread=False: the web server reuses one connection from
    # FastAPI's worker threads, serialised by a lock in server.py.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # FK enforcement is per-connection
    # WAL: readers never block the writer, and crash recovery is stronger than
    # the default rollback journal. synchronous=NORMAL is the recommended
    # pairing (durable at WAL-checkpoint granularity). In-memory DBs (eval,
    # tests) keep journal_mode=memory, which is expected.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")  # a second connection may write
    # order matters: migrate the columns an older database is missing, then
    # let the schema create whatever tables are still absent
    _migrate(conn)
    conn.executescript(_SCHEMA)
    return conn


_SCHEMA_VERSION = 1  # bumped when _migrate learns a new step


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current schema, idempotently.
    CREATE IF NOT EXISTS covers new tables; column additions land here. The
    user_version stamp makes this a no-op after a database's first connect."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= _SCHEMA_VERSION:
        return
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "misspellings" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(misspellings)")}
        if "guard_context" not in cols:
            conn.execute("ALTER TABLE misspellings ADD COLUMN "
                         "guard_context TEXT NOT NULL DEFAULT '[]'")
    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    conn.commit()


@dataclass
class Term:
    id: int
    preferred: str
    kind: str | None
    note: str | None
    trust: int
    source: str
    misspellings: dict[str, dict] = field(default_factory=dict)  # -> {seen, vetoes, guard}

    @property
    def trusted(self) -> bool:
        return self.trust >= ACTIVE_TRUST

    @property
    def status(self) -> str:
        if self.trust <= 0:
            return "muted"
        return "trusted" if self.trusted else "watching"

    def as_dict(self) -> dict:
        """The one projection served by the API, eval records, and snapshots."""
        return {"preferred": self.preferred, "kind": self.kind, "note": self.note,
                "trust": self.trust, "status": self.status,
                "source": self.source, "misspellings": self.misspellings}


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- events -----------------------------------------------------------
    def append_event(self, type_: str, payload: dict, ts: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO events (ts, type, payload) VALUES (?, ?, ?)",
            (ts or now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
        )
        return cur.lastrowid

    def events(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM events ORDER BY id").fetchall()

    # -- terms ------------------------------------------------------------
    def get_term(self, preferred: str) -> Term | None:
        row = self.conn.execute(
            "SELECT * FROM terms WHERE preferred_cf = ?", (_cf(preferred),)
        ).fetchone()
        return self._hydrate(row) if row else None

    def all_terms(self) -> list[Term]:
        rows = self.conn.execute("SELECT * FROM terms ORDER BY preferred_cf").fetchall()
        return [self._hydrate(r) for r in rows]

    def find_term_by_key(self, key: str) -> Term | None:
        """Find a term whose preferred is a phonetic variant of `key` - so a
        user alternating spellings (Adithya/Aaditya) reinforces ONE term
        instead of accidentally creating same-sound duplicates."""
        from .phonetics import phrase_key
        for term in self.all_terms():
            if phrase_key(term.preferred) == key:
                return term
        return None

    def _hydrate(self, row: sqlite3.Row) -> Term:
        term = Term(row["id"], row["preferred"], row["kind"], row["note"],
                    row["trust"], row["source"])
        for s in self.conn.execute(
            "SELECT misspelling, seen, vetoes, guard_context FROM misspellings "
            "WHERE term_id = ?", (row["id"],)
        ):
            term.misspellings[s["misspelling"]] = {
                "seen": s["seen"], "vetoes": s["vetoes"],
                "guard": json.loads(s["guard_context"])}
        return term

    def upsert_term(self, preferred: str, kind: str | None, note: str | None,
                    trust: int, source: str, ts: str,
                    update_preferred: bool = False) -> Term:
        """Single-statement upsert (no check-then-insert race).

        `update_preferred` rewrites the display spelling - an explicit add
        means "this is how I want it written", so it wins over a previously
        learned casing; implicit learning leaves the existing spelling alone.
        """
        self.conn.execute(
            "INSERT INTO terms (preferred, preferred_cf, kind, note, trust, "
            "source, created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (preferred_cf) DO UPDATE SET "
            "preferred = CASE WHEN ? THEN excluded.preferred ELSE preferred END, "
            "trust = excluded.trust, "
            "kind = COALESCE(excluded.kind, kind), "
            "note = COALESCE(excluded.note, note), "
            "updated_ts = excluded.updated_ts",
            (preferred, _cf(preferred), kind, note, trust, source, ts, ts,
             1 if update_preferred else 0),
        )
        return self.get_term(preferred)  # type: ignore[return-value]

    def bump_trust(self, term_id: int, delta: int, ts: str) -> None:
        self.conn.execute(
            "UPDATE terms SET trust = trust + ?, updated_ts = ? WHERE id = ?",
            (delta, ts, term_id),
        )

    def delete_term(self, preferred: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM terms WHERE preferred_cf = ?", (_cf(preferred),)
        )
        return cur.rowcount > 0

    # -- misspellings -----------------------------------------------------
    def record_misspelling(self, term_id: int, misspelling: str, ts: str,
                           seen_delta: int = 0, veto_delta: int = 0,
                           guard_words: set[str] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO misspellings (term_id, misspelling, seen, vetoes, last_ts) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (term_id, misspelling) DO UPDATE SET "
            "seen = seen + excluded.seen, vetoes = vetoes + excluded.vetoes, "
            "last_ts = excluded.last_ts",
            (term_id, misspelling.casefold(), seen_delta, veto_delta, ts),
        )
        if guard_words:
            row = self.conn.execute(
                "SELECT guard_context FROM misspellings WHERE term_id = ? AND misspelling = ?",
                (term_id, misspelling.casefold())).fetchone()
            # newest restraint first: fresh guard words always survive the
            # cap, older ones are evicted (alphabetical eviction would let a
            # full cache silently discard newly learned restraint)
            fresh = sorted(guard_words)
            merged = (fresh + [w for w in json.loads(row["guard_context"])
                               if w not in guard_words])[:24]
            self.conn.execute(
                "UPDATE misspellings SET guard_context = ? WHERE term_id = ? AND misspelling = ?",
                (json.dumps(merged), term_id, misspelling.casefold()))

    # -- retired terms (a delete that stays deleted) ----------------------
    def retire(self, preferred: str, key: str, ts: str) -> None:
        self.conn.execute(
            "INSERT INTO retired (preferred, preferred_cf, key, ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (preferred_cf) DO UPDATE SET key = excluded.key, "
            "ts = excluded.ts",
            (preferred, _cf(preferred), key, ts))

    def unretire(self, preferred: str, key: str = "") -> None:
        # lift retirement for the exact name and for phonetic variants of it
        # (re-adding "Dhruv" must also unblock a retired "Dhruvv")
        self.conn.execute("DELETE FROM retired WHERE preferred_cf = ? OR key = ?",
                          (_cf(preferred), key or "\x00"))

    def is_retired(self, key: str) -> str | None:
        """Return the retired preferred whose phonetic key matches, if any."""
        row = self.conn.execute("SELECT preferred FROM retired WHERE key = ?",
                                (key,)).fetchone()
        return row["preferred"] if row else None

    # -- traces -----------------------------------------------------------
    def add_trace(self, input_text: str, output_text: str, data: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO traces (ts, input, output, data) VALUES (?, ?, ?, ?)",
            (now_iso(), input_text, output_text, json.dumps(data, ensure_ascii=False)),
        )
        self.conn.commit()
        return cur.lastrowid

    def traces(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM traces ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "input": r["input"],
                 "output": r["output"], **json.loads(r["data"])} for r in rows]

    # -- maintenance ------------------------------------------------------
    # what the event log can rebuild. `traces` is deliberately not here: it
    # records what was decided at runtime, which no event replays, so a
    # rebuild must leave it alone or that history is gone for good.
    _FOLDED_TABLES = ("misspellings", "terms", "retired")

    def clear_state(self) -> None:
        """Wipe folded state, keep the event log and the decision traces."""
        for table in self._FOLDED_TABLES:
            self.conn.execute(f"DELETE FROM {table}")

    def clear_all(self) -> None:
        """Wipe everything, event log and traces included. Deliberately does
        NOT commit: the caller (engine.reset) wraps wipe + re-seed in one
        transaction."""
        for table in self._FOLDED_TABLES + ("traces", "events"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("DELETE FROM sqlite_sequence")   # ids restart at 1 too

    def row_counts(self) -> dict[str, int]:
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("events",) + self._FOLDED_TABLES + ("traces",)}
