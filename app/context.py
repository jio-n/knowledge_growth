"""Context builder for LLM calls (§9).

Selects and compresses what goes to the model instead of dumping the
whole document: selection + N surrounding blocks + heading path + a
digest of the user's accumulated understanding note. Everything sent is
recorded in answers.context_summary for traceability (§10).
"""
from __future__ import annotations

import json
import sqlite3

from .config import context_config, note_template


def surrounding_context(con: sqlite3.Connection, source_id: str,
                        block_idx: int | None) -> tuple[str, str, str]:
    """Return (before, after, heading_path) around a block index."""
    if block_idx is None:
        return "", "", ""
    cfg = context_config()
    n = cfg["surrounding_blocks"]
    rows = con.execute(
        """SELECT idx, kind, text, heading_path FROM document_blocks
           WHERE source_id = ? AND idx BETWEEN ? AND ? ORDER BY idx""",
        (source_id, block_idx - n, block_idx + n),
    ).fetchall()
    before, after, heading = [], [], ""
    limit = cfg["max_context_chars"] // 2
    for r in rows:
        if r["idx"] == block_idx:
            heading = r["heading_path"] or ""
        elif r["idx"] < block_idx:
            before.append(r["text"])
        else:
            after.append(r["text"])
    return ("\n".join(before)[-limit:], "\n".join(after)[:limit], heading)


def note_digest(con: sqlite3.Connection, source_id: str) -> str:
    """Compact digest of the understanding note so answers can build on
    what the user already understands."""
    cfg = context_config()
    labels = {s["key"]: s["label"] for s in note_template()}
    rows = con.execute(
        """SELECT section_key, title, content FROM knowledge_items
           WHERE source_id = ? ORDER BY updated_at DESC LIMIT ?""",
        (source_id, cfg["note_digest_items"]),
    ).fetchall()
    if not rows:
        return "(まだノートはありません)"
    lines = []
    for r in rows:
        label = labels.get(r["section_key"], r["section_key"])
        snippet = (r["title"] or r["content"])[:120].replace("\n", " ")
        lines.append(f"- [{label}] {snippet}")
    return "\n".join(lines)


def doc_excerpt(con: sqlite3.Connection, source_id: str, max_chars: int = 24000) -> str:
    """Document excerpt for initial extraction: front-loaded (title,
    abstract, intro) plus tail (conclusion) if budget remains."""
    rows = con.execute(
        "SELECT kind, text, heading_path FROM document_blocks WHERE source_id = ? ORDER BY idx",
        (source_id,),
    ).fetchall()
    parts, used = [], 0
    for r in rows:
        t = ("## " + r["text"]) if r["kind"] == "heading" else r["text"]
        if used + len(t) > max_chars * 0.8:
            break
        parts.append(t)
        used += len(t)
    # tail (conclusion) — last few blocks
    tail = []
    for r in rows[-8:]:
        t = ("## " + r["text"]) if r["kind"] == "heading" else r["text"]
        if used + len(t) > max_chars:
            break
        tail.append(t)
        used += len(t)
    if tail and rows and len(rows) > 16:
        parts.append("\n...(中略)...\n")
        parts.extend(tail)
    return "\n\n".join(parts)


def context_record(**kwargs) -> str:
    """JSON blob stored on answers describing what was sent to the LLM."""
    return json.dumps(kwargs, ensure_ascii=False)
