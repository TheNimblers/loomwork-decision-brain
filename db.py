import hashlib
import os
import sqlite3
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DATABASE_PATH", "./brain.db")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    conn = get_db()
    try:
        conn.executescript(
            """
-- SOURCES: raw inputs, content-addressed
CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    content       TEXT NOT NULL,
    document_date TEXT,
    ingested_at   TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(
    title, content,
    content='sources', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS sources_fts_insert AFTER INSERT ON sources BEGIN
    INSERT INTO sources_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS sources_fts_delete AFTER DELETE ON sources BEGIN
    INSERT INTO sources_fts(sources_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS sources_fts_update AFTER UPDATE ON sources BEGIN
    INSERT INTO sources_fts(sources_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
    INSERT INTO sources_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

-- FACTS: bi-temporal typed atoms
CREATE TABLE IF NOT EXISTS facts (
    id              TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    fact_type       TEXT NOT NULL,
    claim           TEXT NOT NULL,
    verbatim_quote  TEXT NOT NULL,
    evidence_tier   TEXT NOT NULL,
    confidence      REAL NOT NULL,
    conditions      TEXT,
    entities        TEXT,
    numeric_value   REAL,
    numeric_unit    TEXT,
    valid_from      TEXT,
    learned_at      TEXT NOT NULL,
    superseded_by   TEXT,
    contested       INTEGER DEFAULT 0,
    FOREIGN KEY (source_id)    REFERENCES sources(id),
    FOREIGN KEY (superseded_by) REFERENCES facts(id)
);

CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source_id);
CREATE INDEX IF NOT EXISTS idx_facts_contested ON facts(contested);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact_type, claim, verbatim_quote, conditions,
    content='facts', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS facts_fts_insert AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, fact_type, claim, verbatim_quote, conditions)
    VALUES (new.rowid, new.fact_type, new.claim, new.verbatim_quote, new.conditions);
END;

CREATE TRIGGER IF NOT EXISTS facts_fts_delete AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact_type, claim, verbatim_quote, conditions)
    VALUES ('delete', old.rowid, old.fact_type, old.claim, old.verbatim_quote, old.conditions);
END;

CREATE TRIGGER IF NOT EXISTS facts_fts_update AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact_type, claim, verbatim_quote, conditions)
    VALUES ('delete', old.rowid, old.fact_type, old.claim, old.verbatim_quote, old.conditions);
    INSERT INTO facts_fts(rowid, fact_type, claim, verbatim_quote, conditions)
    VALUES (new.rowid, new.fact_type, new.claim, new.verbatim_quote, new.conditions);
END;

-- CONTRADICTIONS: detected at write-time
CREATE TABLE IF NOT EXISTS contradictions (
    id                TEXT PRIMARY KEY,
    fact_a_id         TEXT NOT NULL,
    fact_b_id         TEXT NOT NULL,
    conflict_type     TEXT NOT NULL,
    description       TEXT NOT NULL,
    severity          TEXT NOT NULL,
    detected_at       TEXT NOT NULL,
    resolved_at       TEXT,
    resolution_note   TEXT,
    FOREIGN KEY (fact_a_id) REFERENCES facts(id),
    FOREIGN KEY (fact_b_id) REFERENCES facts(id)
);

-- ENTITIES: people, companies, investors
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    aliases         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    fact_id   TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (fact_id, entity_id),
    FOREIGN KEY (fact_id)   REFERENCES facts(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

-- ENTITY RELATIONSHIPS: typed directed edges
CREATE TABLE IF NOT EXISTS entity_relationships (
    id            TEXT PRIMARY KEY,
    entity_a_id   TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    entity_b_id   TEXT NOT NULL,
    source_id     TEXT,
    fact_id       TEXT,
    learned_at    TEXT NOT NULL,
    FOREIGN KEY (entity_a_id) REFERENCES entities(id),
    FOREIGN KEY (entity_b_id) REFERENCES entities(id),
    FOREIGN KEY (source_id)   REFERENCES sources(id),
    FOREIGN KEY (fact_id)     REFERENCES facts(id)
);

CREATE INDEX IF NOT EXISTS idx_rel_entity_a ON entity_relationships(entity_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_entity_b ON entity_relationships(entity_b_id);
CREATE INDEX IF NOT EXISTS idx_rel_type     ON entity_relationships(relation_type);

-- DECISIONS: append-only, the closed loop
CREATE TABLE IF NOT EXISTS decisions (
    id                    TEXT PRIMARY KEY,
    question              TEXT NOT NULL,
    retrieved_fact_ids    TEXT NOT NULL,
    contradictions_found  TEXT,
    research_triggered    INTEGER DEFAULT 0,
    research_queries      TEXT,
    research_fact_ids     TEXT,
    synthesis             TEXT NOT NULL,
    confidence            REAL NOT NULL,
    recommended_action    TEXT NOT NULL,
    open_gaps             TEXT,
    key_contradiction     TEXT,
    human_decision        TEXT,
    decision_note         TEXT,
    created_at            TEXT NOT NULL
);
"""
        )
        conn.commit()
    finally:
        conn.close()
