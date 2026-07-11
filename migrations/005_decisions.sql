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
