-- Dedicated Brief storage; existing research objects are untouched.
CREATE TABLE paper_briefs (
  source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
  schema_version TEXT NOT NULL CHECK (schema_version = 'paper-brief-0.1'),
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE paper_brief_fields (
  source_id TEXT NOT NULL REFERENCES paper_briefs(source_id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  value_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('confirmed','derived','uncertain','not_reported','not_applicable')),
  evidence_json TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('source','llm','auto_extract','chatgpt_import','user')),
  user_edited INTEGER NOT NULL CHECK (user_edited IN (0,1)),
  provenance_json TEXT NOT NULL,
  verification TEXT NOT NULL DEFAULT 'unverified' CHECK (verification IN ('unverified','verified','disputed')),
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, field_name)
);
CREATE TABLE import_packages (
  package_id TEXT PRIMARY KEY,
  payload_hash TEXT NOT NULL UNIQUE,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  manifest_json TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  preview_json TEXT NOT NULL,
  imported_at TEXT NOT NULL
);
CREATE TABLE import_previews (
  id TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  options_json TEXT NOT NULL,
  preview_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  committed_at TEXT
);
