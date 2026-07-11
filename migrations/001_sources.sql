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
