-- Staging/lifecycle only. Accepted Brief fields still use paper_brief_fields.
CREATE TABLE paper_brief_generations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('generating','preview','completed','failed')),
    snapshot_json TEXT NOT NULL,
    preview_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_brief_generation ON paper_brief_generations(source_id)
    WHERE state='generating';
CREATE INDEX brief_generation_history ON paper_brief_generations(source_id,created_at);
