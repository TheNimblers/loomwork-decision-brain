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
