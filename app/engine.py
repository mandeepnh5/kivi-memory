"""Facade: event dispatch, replay, reset. The one write-path into the system.

Every state change enters as an event; the folded tables are always
reconstructible with `replay()`. Reset = wipe + re-append the seed events.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

from . import learning
from .config import ACTIVE_TRUST
from .phonetics import phrase_key
from .store import Store, now_iso
from .textutil import words

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "seed_events.jsonl"


class Engine:
    def __init__(self, store: Store):
        self.store = store

    # -- public write path -------------------------------------------------
    def submit(self, type_: str, payload: dict) -> dict:
        """Append an event and fold it, atomically: a fold failure rolls the
        event back too, so the log and the folded state can never diverge."""
        ts = now_iso()
        try:
            self.store.append_event(type_, payload, ts)
            effects = self._fold(type_, payload, ts)
            self.store.conn.commit()
            return effects
        except Exception:
            self.store.conn.rollback()
            raise

    def observe(self, asr: str, formatted: str, final: str,
                presented: str | None = None) -> dict:
        if not formatted.strip() and not final.strip():
            # nothing to diff, so nothing to learn. A delete that changes
            # nothing earns no event either; an append-only log is only
            # trustworthy if every row in it meant something.
            return learning.ObservationReport().as_dict()
        payload = {"asr": asr, "formatted": formatted, "final": final}
        if presented:
            payload["presented"] = presented
        return self.submit("observation", payload)

    def add_term(self, preferred: str, kind: str | None = None,
                 note: str | None = None) -> dict:
        # NFC + whitespace normalization: "Zoe" + a combining diaeresis (NFD,
        # what some IMEs emit) must be the same term as the precomposed "Zoe"
        preferred = unicodedata.normalize("NFC", " ".join(preferred.split()))
        if not phrase_key(preferred) or not any(c.isalpha() for c in preferred):
            # empty, punctuation-only or digit-only names are noise: they can
            # never be a spoken word, and a digit-only term would rewrite
            # quantities - reject before the event is logged
            return {"error": "preferred must contain at least one letter"}
        if len(preferred) > 64:
            return {"error": "preferred is too long (64 characters max)"}
        if words(preferred) != preferred.split():
            # the tokenizer must round-trip the spelling exactly, or applying
            # it would splice in text retrieval can never re-match ("GPT-4"
            # would grow a stray "-4" on every run; write "GPT4")
            return {"error": "preferred may use letters, digits and spaces only"}
        return self.submit("add_term",
                           {"preferred": preferred, "kind": kind, "note": note})

    def delete_term(self, preferred: str) -> dict:
        if self.store.get_term(preferred) is None:
            return {"deleted": False}   # a no-op earns no event in the log
        return self.submit("delete_term", {"preferred": preferred})

    # -- fold --------------------------------------------------------------
    def _fold(self, type_: str, payload: dict, ts: str) -> dict:
        if type_ == "add_term":
            self.store.unretire(payload["preferred"],
                                phrase_key(payload["preferred"]))  # explicit re-add wins
            existing = self.store.get_term(payload["preferred"])
            trust = max(existing.trust if existing else 0, ACTIVE_TRUST)
            term = self.store.upsert_term(
                payload["preferred"], payload.get("kind"), payload.get("note"),
                trust=trust, source="added", ts=ts,
                update_preferred=True)  # an explicit add sets the spelling
            return {"term": term.preferred, "status": term.status}
        if type_ == "delete_term":
            term = self.store.get_term(payload["preferred"])
            deleted = self.store.delete_term(payload["preferred"])
            if term is not None:
                # a delete stays deleted: remember the phonetic key so the
                # term is not silently relearned from the same evidence
                self.store.retire(term.preferred, phrase_key(term.preferred), ts)
            return {"deleted": deleted}
        if type_ == "set_trust":
            term = self.store.get_term(payload["preferred"])
            if term is None:
                return {"error": f"no such term: {payload['preferred']}"}
            self.store.bump_trust(term.id, payload["trust"] - term.trust, ts)
            return {"term": term.preferred, "trust": payload["trust"]}
        if type_ == "observation":
            # the raw ASR line lives in the event as evidence of what was
            # heard; learning only ever diffs formatted against final
            report = learning.process_observation(
                self.store, payload["formatted"], payload["final"],
                ts=ts, presented=payload.get("presented"))
            return report.as_dict()
        raise ValueError(f"unknown event type: {type_}")

    # -- maintenance -------------------------------------------------------
    def replay(self) -> int:
        """Rebuild folded state from the event log, in one transaction.
        Returns the number of events replayed."""
        events = self.store.events()
        try:
            self.store.clear_state()
            for row in events:
                self._fold(row["type"], json.loads(row["payload"]), row["ts"])
            self.store.conn.commit()
        except Exception:
            self.store.conn.rollback()
            raise
        return len(events)

    def reset(self, seed_path: Path | None = None) -> dict:
        """Wipe everything and re-seed, atomically. The seed file is parsed
        up front and wipe + replay run in one transaction, so a bad seed
        leaves the existing database completely untouched."""
        path = seed_path or SEED_PATH
        seed: list[tuple[str, dict]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                event = json.loads(line)   # malformed seed fails BEFORE the wipe
                seed.append((event["type"], event["payload"]))
        try:
            self.store.clear_all()
            for type_, payload in seed:
                ts = now_iso()
                self.store.append_event(type_, payload, ts)
                self._fold(type_, payload, ts)
            self.store.conn.commit()
        except Exception:
            self.store.conn.rollback()
            raise
        return {"seed_events": len(seed), "rows": self.store.row_counts()}
