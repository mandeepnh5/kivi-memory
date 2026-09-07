-- Kivi word-memory schema.
--
-- `events` is the ground truth: an append-only log of everything the system
-- has ever been told. `terms` and `misspellings` are a deterministic fold over it
-- (replayable via reset), as is `retired`; `traces` records every decision.

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,               -- ISO-8601 UTC
    type    TEXT NOT NULL,               -- add_term | delete_term | set_trust | observation
    payload TEXT NOT NULL                -- JSON
);

CREATE TABLE IF NOT EXISTS terms (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    preferred    TEXT NOT NULL,          -- as the user wants it written
    preferred_cf TEXT NOT NULL UNIQUE,   -- casefolded, for lookups
    kind         TEXT,                   -- free-form: person, product, org, place, ...
    note         TEXT,                   -- context hint, used by the LLM judge
    trust        INTEGER NOT NULL,       -- signed; >= ACTIVE_TRUST fires
    source       TEXT NOT NULL,          -- added | learned
    created_ts   TEXT NOT NULL,
    updated_ts   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS misspellings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    term_id INTEGER NOT NULL REFERENCES terms(id) ON DELETE CASCADE,
    misspelling TEXT NOT NULL,               -- casefolded spelling ASR produced
    seen    INTEGER NOT NULL DEFAULT 0,
    vetoes  INTEGER NOT NULL DEFAULT 0,  -- times the user reverted this misspelling
    -- context guard: JSON list of content words. Empty list + vetoes>0 means
    -- "never touch this misspelling" (name-like words); a non-empty list means
    -- "leave it alone only in contexts sharing these words" (ordinary words,
    -- e.g. kiwi guarded in food sentences, still correctable elsewhere).
    guard_context TEXT NOT NULL DEFAULT '[]',
    last_ts TEXT NOT NULL,
    UNIQUE (term_id, misspelling)
);

-- retired terms: a deleted term must not be silently relearned from the same
-- kind of evidence. An explicit re-add lifts the retirement.
CREATE TABLE IF NOT EXISTS retired (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    preferred    TEXT NOT NULL,
    preferred_cf TEXT NOT NULL UNIQUE,
    key          TEXT NOT NULL,          -- phonetic key at deletion time
    ts           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    input  TEXT NOT NULL,
    output TEXT NOT NULL,
    data   TEXT NOT NULL                 -- JSON: candidates, decisions, llm, timing
);

CREATE INDEX IF NOT EXISTS idx_misspellings_term ON misspellings(term_id);
CREATE INDEX IF NOT EXISTS idx_retired_key  ON retired(key);
