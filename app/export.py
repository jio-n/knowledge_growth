"""Markdown / JSON export (§18, §19).

Format spec: docs/architecture/export_spec.md. The Markdown is designed
to be readable standalone (Obsidian-friendly): YAML frontmatter,
origin/type labels on every item, and original-text anchors as quotes.
"""
from __future__ import annotations

import json
import sqlite3

from .config import note_template
from .db import rows_to_dicts

ORIGIN_LABEL = {
    "source_quote": "原文",
    "auto_extract": "AI自動抽出",
    "llm": "AI回答",
    "llm_edited": "AI回答(ユーザー編集済)",
    "user": "ユーザー",
}
INFO_LABEL = {
    "fact": "事実", "claim": "著者の主張", "result": "実験結果",
    "llm_summary": "AI要約", "llm_interpretation": "AI解釈",
    "user_thought": "考察", "open_question": "未解決", "idea": "アイデア",
    "term": "用語", "action": "アクション", "translation": "翻訳",
}


def note_markdown(con: sqlite3.Connection, source_id: str) -> str:
    src = dict(con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
    items = rows_to_dicts(con.execute(
        "SELECT * FROM knowledge_items WHERE source_id=? ORDER BY section_key, sort_order, created_at",
        (source_id,)))
    questions = rows_to_dicts(con.execute(
        """SELECT q.*, a.content AS answer, a.model AS answer_model FROM questions q
           LEFT JOIN answers a ON a.question_id = q.id
           WHERE q.source_id=? ORDER BY q.created_at""", (source_id,)))
    tags = [r["name"] for r in con.execute(
        "SELECT t.name FROM tags t JOIN source_tags st ON st.tag_id=t.id WHERE st.source_id=?",
        (source_id,))]

    authors = json.loads(src.get("authors") or "[]")
    fm = {
        "title": src["title"],
        "type": src["type"],
        "authors": authors,
        "year": src.get("year"),
        "url": src.get("url"),
        "doi": src.get("doi"),
        "arxiv_id": src.get("arxiv_id"),
        "tags": tags,
        "reading_status": src.get("reading_status"),
        "created": src.get("created_at"),
        "updated": src.get("updated_at"),
    }
    lines = ["---"]
    for k, v in fm.items():
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines += ["---", "", f"# {src['title']}", ""]
    if src.get("one_line_summary"):
        lines += [f"> **一言要約**: {src['one_line_summary']}", ""]

    by_section: dict[str, list[dict]] = {}
    for it in items:
        by_section.setdefault(it["section_key"], []).append(it)

    for sec in note_template():
        sec_items = by_section.pop(sec["key"], [])
        if not sec_items:
            continue
        lines += [f"## {sec['label']}", ""]
        for it in sec_items:
            lines += _item_md(it)
    for key, sec_items in by_section.items():  # sections not in template
        lines += [f"## {key}", ""]
        for it in sec_items:
            lines += _item_md(it)

    if questions:
        lines += ["## 質問と回答の履歴", ""]
        for q in questions:
            lines.append(f"### Q: {q['question_text']}")
            anchor = _anchor_line(q.get("anchor"))
            if anchor:
                lines.append(anchor)
            if q.get("selection_text"):
                lines.append(f"> {q['selection_text'][:300]}")
            lines.append("")
            if q.get("answer"):
                lines.append(f"**A** _(AI回答 / {q.get('answer_model') or '?'})_:")
                lines.append("")
                lines.append(q["answer"])
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def _item_md(it: dict) -> list[str]:
    origin = ORIGIN_LABEL.get(it["origin"], it["origin"])
    info = INFO_LABEL.get(it["info_type"], it["info_type"])
    head = f"**{it['title']}** — " if it.get("title") else ""
    out = [f"- {head}{it['content']}".replace("\n", "\n  ")]
    meta = f"  - _出所: {origin} / 種別: {info}"
    if it.get("verification") and it["verification"] != "unverified":
        meta += f" / 検証: {it['verification']}"
    meta += "_"
    out.append(meta)
    anchor = _anchor_line(it.get("anchor"), indent="  ")
    if anchor:
        out.append(anchor)
    out.append("")
    return out


def _anchor_line(anchor_json: str | None, indent: str = "") -> str | None:
    if not anchor_json:
        return None
    try:
        a = json.loads(anchor_json)
    except Exception:
        return None
    parts = []
    if a.get("page"):
        parts.append(f"p.{a['page']}")
    if a.get("headingPath"):
        parts.append(a["headingPath"])
    if a.get("quote"):
        parts.append(f"“{a['quote'][:80]}…”" if len(a.get("quote", "")) > 80 else f"“{a['quote']}”")
    if not parts:
        return None
    return f"{indent}- _原文: {' | '.join(parts)}_"


def source_json(con: sqlite3.Connection, source_id: str) -> dict:
    """Full portable dump of one source (§19)."""
    def q(sql, *p):
        return rows_to_dicts(con.execute(sql, p))
    src = dict(con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
    return {
        "source": src,
        "versions": q("SELECT * FROM source_versions WHERE source_id=?", source_id),
        "blocks": q("SELECT * FROM document_blocks WHERE source_id=? ORDER BY idx", source_id),
        "questions": q("SELECT * FROM questions WHERE source_id=?", source_id),
        "answers": q("""SELECT a.* FROM answers a JOIN questions qs ON a.question_id=qs.id
                        WHERE qs.source_id=?""", source_id),
        "knowledge_items": q("SELECT * FROM knowledge_items WHERE source_id=?", source_id),
        "highlights": q("SELECT * FROM highlights WHERE source_id=?", source_id),
        "translations": q("SELECT * FROM translations WHERE source_id=?", source_id),
        "tags": [r["name"] for r in con.execute(
            "SELECT t.name FROM tags t JOIN source_tags st ON st.tag_id=t.id WHERE st.source_id=?",
            (source_id,))],
    }
