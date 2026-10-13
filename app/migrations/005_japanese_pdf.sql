-- Existing translations remain the sole store for block translations.
ALTER TABLE translations ADD COLUMN original_page INTEGER;
ALTER TABLE translations ADD COLUMN original_bbox TEXT;
ALTER TABLE translations ADD COLUMN source_hash TEXT;
ALTER TABLE translations ADD COLUMN runtime TEXT;
ALTER TABLE translations ADD COLUMN prompt_version TEXT;
ALTER TABLE translations ADD COLUMN created_at TEXT;
ALTER TABLE translations ADD COLUMN translation_status TEXT NOT NULL DEFAULT 'translated'
    CHECK(translation_status IN ('translated','skipped_protected','skipped_unsafe','overflow','failed'));
ALTER TABLE translations ADD COLUMN reason TEXT;
UPDATE translations SET created_at=updated_at;
-- A derived file lifecycle/report, not a second translation text store.
CREATE TABLE japanese_pdf_generations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_version_id TEXT NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL,
    document_digest TEXT NOT NULL,
    pages_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('generating','preview_ready','failed')),
    file_hash TEXT,
    report_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_japanese_pdf ON japanese_pdf_generations(source_id) WHERE state='generating';
CREATE INDEX japanese_pdf_history ON japanese_pdf_generations(source_id,created_at);
