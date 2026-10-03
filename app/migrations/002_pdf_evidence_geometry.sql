-- Additive only: preserve legacy block IDs, translations and anchor JSON.
-- NULL geometry means unknown; never invent coordinates for existing data.
ALTER TABLE document_blocks ADD COLUMN bbox_json TEXT;
ALTER TABLE document_blocks ADD COLUMN role TEXT;
