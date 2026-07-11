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
