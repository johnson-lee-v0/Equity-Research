CREATE TABLE IF NOT EXISTS source_documents (
    source_id TEXT PRIMARY KEY REFERENCES sources(id),
    namespace TEXT NOT NULL,
    original_hash TEXT NOT NULL,
    original_bytes BLOB NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
