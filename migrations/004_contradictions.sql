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
