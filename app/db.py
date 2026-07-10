"""SQLite persistence layer (stdlib sqlite3, no ORM).

Schema documented in docs/architecture/data_model.md — keep both in sync.
One connection per request via `get_db()`; WAL mode for concurrent reads.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,                 -- pdf | web | text | markdown
  title TEXT NOT NULL,
  authors TEXT DEFAULT '[]',          -- JSON array of strings
  year INTEGER,
  venue TEXT,
  url TEXT,
  canonical_url TEXT,
  doi TEXT,
  arxiv_id TEXT,
  site_name TEXT,
  published_at TEXT,
  lang TEXT,
  content_hash TEXT,
  file_path TEXT,                     -- original file (pdf/snapshot), relative to data/files
  reading_status TEXT DEFAULT 'unread',   -- unread | reading | read | recheck
  importance INTEGER DEFAULT 0,
  analysis_status TEXT DEFAULT 'pending', -- pending | running | done | error | skipped
  analysis_error TEXT,
  one_line_summary TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_opened_at TEXT
);

CREATE TABLE IF NOT EXISTS source_versions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  fetched_at TEXT NOT NULL,
  content_hash TEXT,
  raw_path TEXT,                      -- snapshot of raw fetched content
  note TEXT
);

-- Extracted document structure: ordered blocks per version.
CREATE TABLE IF NOT EXISTS document_blocks (
  id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES source_versions(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  kind TEXT NOT NULL,                 -- heading | para | code | quote | list | table | figure
  level INTEGER,                      -- heading level
  text TEXT NOT NULL,
  page INTEGER,                       -- PDF page number (1-based)
  heading_path TEXT                   -- "3 Method > 3.2 Loss"
);
CREATE INDEX IF NOT EXISTS idx_blocks_version ON document_blocks(version_id, idx);

CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY,
  name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS source_tags (
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (source_id, tag_id)
);

CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  version_id TEXT,
  anchor TEXT,                        -- JSON SourceAnchor (see source_anchor_spec.md)
  selection_text TEXT,
  prompt_type TEXT,                   -- explain | explain_simple | critique | translate | free ...
  question_text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answers (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  content TEXT NOT NULL,              -- markdown
  provider TEXT,
  model TEXT,
  prompt_id TEXT,
  prompt_version TEXT,
  context_summary TEXT,               -- JSON: what was sent to the LLM
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_items (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  section_key TEXT NOT NULL,          -- note_template key
  title TEXT,
  content TEXT NOT NULL,              -- markdown
  origin TEXT NOT NULL,               -- source_quote | llm | llm_edited | user | auto_extract
  info_type TEXT NOT NULL,            -- fact | claim | result | llm_summary | llm_interpretation |
                                      -- user_thought | open_question | idea | term | action | translation
  verification TEXT DEFAULT 'unverified',  -- unverified | verified | disputed
  anchor TEXT,                        -- JSON SourceAnchor
  question_id TEXT,
  answer_id TEXT,
  sort_order REAL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_source ON knowledge_items(source_id, section_key, sort_order);

CREATE TABLE IF NOT EXISTS highlights (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  version_id TEXT,
  anchor TEXT NOT NULL,
  color TEXT DEFAULT 'yellow',
  comment TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translations (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  version_id TEXT,
  block_id TEXT,                      -- NULL for free selection translation
  source_text TEXT NOT NULL,
  translated_text TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  user_edited INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_translations_block ON translations(source_id, block_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def get_db() -> sqlite3.Connection:
    ensure_dirs()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    con = get_db()
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]
