"""Knowledge item CRUD, save-target suggestion, note assembly, search (§11, §14, §17)."""
from __future__ import annotations

import json

from fastapi import Request, APIRouter, HTTPException
from pydantic import BaseModel

from .config import llm_config, note_template
from .db import get_db, new_id, now, rows_to_dicts
from .llm import get_provider
from .llm.base import strip_json_fences
from .prompts import get_prompt

router = APIRouter(prefix="/api")

# prompt_type -> (section_key, info_type) heuristic used before any LLM refinement (ai_pipeline.md パイプライン4)
HEURISTIC_MAP = {
    "critique": ("limitations", "llm_interpretation"),
    "apply": ("applications", "idea"),
    "translate": ("terms", "translation"),
    "math": ("method", "llm_interpretation"),
}
DEFAULT_SECTION_INFO = ("insights", "llm_interpretation")

# info_type vocabulary (data_model.md / requirements.md §46-47)
ALLOWED_INFO_TYPES = {
    "fact", "claim", "result", "llm_summary", "llm_interpretation",
    "user_thought", "open_question", "idea", "term", "action", "translation",
}


def _item_row(con, item_id: str) -> dict:
    row = dict(con.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone())
    row["anchor"] = json.loads(row["anchor"]) if row["anchor"] else None
    return row


class KnowledgeIn(BaseModel):
    source_id: str
    section_key: str
    title: str | None = None
    content: str
    origin: str
    info_type: str
    anchor: dict | None = None
    question_id: str | None = None
    answer_id: str | None = None


@router.post("/knowledge")
def create_knowledge(body: KnowledgeIn):
    con = get_db()
    try:
        if not con.execute("SELECT 1 FROM sources WHERE id=?", (body.source_id,)).fetchone():
            raise HTTPException(404, "資料が見つかりません")
        row = con.execute(
            "SELECT MAX(sort_order) AS m FROM knowledge_items WHERE source_id=? AND section_key=?",
            (body.source_id, body.section_key)).fetchone()
        sort_order = (row["m"] + 1) if row and row["m"] is not None else 1
        iid, ts = new_id(), now()
        con.execute(
            """INSERT INTO knowledge_items
               (id, source_id, section_key, title, content, origin, info_type, anchor,
                question_id, answer_id, sort_order, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, body.source_id, body.section_key, body.title, body.content, body.origin,
             body.info_type, json.dumps(body.anchor, ensure_ascii=False) if body.anchor else None,
             body.question_id, body.answer_id, sort_order, ts, ts))
        con.commit()
        return {"item": _item_row(con, iid)}
    finally:
        con.close()


class SuggestIn(BaseModel):
    source_id: str
    content: str
    prompt_type: str | None = None


@router.post("/knowledge/suggest")
def suggest_save_target(body: SuggestIn, request: Request):
    """Heuristic first, LLM refinement second. Must never 5xx (ai_pipeline.md パイプライン4)."""
    section_key, info_type = HEURISTIC_MAP.get(body.prompt_type, DEFAULT_SECTION_INFO)
    first_line = next((l for l in body.content.strip().splitlines() if l.strip()), "")
    title = first_line.strip()[:30]
    result = {"section_key": section_key, "info_type": info_type, "title": title}

    try:
        cfg = llm_config()
        if cfg.get("provider") != "mock":
            template = note_template()
            sections = "\n".join(f"{s['key']}: {s['label']}" for s in template)
            valid_keys = {s["key"] for s in template}
            prompt = get_prompt("suggest_save_target")
            user_prompt = prompt.render(sections=sections, content=body.content)
            provider = get_provider(request.app.state.ai_runtime)
            res = provider.complete_json(
                "あなたは研究ノートの整理を支援するアシスタントです。", user_prompt)
            data = json.loads(strip_json_fences(res.text))
            if (isinstance(data, dict) and data.get("section_key") in valid_keys
                    and data.get("info_type") in ALLOWED_INFO_TYPES):
                result = {
                    "section_key": data["section_key"],
                    "info_type": data["info_type"],
                    "title": str(data.get("title") or title)[:30],
                }
    except Exception:
        pass  # 絶対にヒューリスティック結果を返す — このAPIは5xxにしない
    return result


@router.get("/sources/{source_id}/note")
def get_note(source_id: str):
    con = get_db()
    try:
        src = con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not src:
            raise HTTPException(404, "資料が見つかりません")
        items = rows_to_dicts(con.execute(
            "SELECT * FROM knowledge_items WHERE source_id=? ORDER BY section_key, sort_order, created_at",
            (source_id,)))
        for it in items:
            it["anchor"] = json.loads(it["anchor"]) if it["anchor"] else None

        by_section: dict[str, list[dict]] = {}
        for it in items:
            by_section.setdefault(it["section_key"], []).append(it)

        template = note_template()
        template_keys = {s["key"] for s in template}
        sections = [{"key": s["key"], "label": s["label"], "items": by_section.get(s["key"], [])}
                    for s in template]
        extra_sections = [{"key": key, "label": key, "items": sec_items}
                           for key, sec_items in by_section.items() if key not in template_keys]
        return {"source": dict(src), "sections": sections, "extra_sections": extra_sections}
    finally:
        con.close()


class KnowledgePatch(BaseModel):
    section_key: str | None = None
    title: str | None = None
    content: str | None = None
    info_type: str | None = None
    verification: str | None = None


@router.patch("/knowledge/{item_id}")
def patch_knowledge(item_id: str, body: KnowledgePatch):
    con = get_db()
    try:
        row = con.execute("SELECT * FROM knowledge_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "知識項目が見つかりません")
        fields: dict = {}
        if body.section_key is not None:
            fields["section_key"] = body.section_key
        if body.title is not None:
            fields["title"] = body.title
        if body.content is not None:
            fields["content"] = body.content
            if row["origin"] == "llm":
                fields["origin"] = "llm_edited"
        if body.info_type is not None:
            fields["info_type"] = body.info_type
        if body.verification is not None:
            fields["verification"] = body.verification
        if fields:
            fields["updated_at"] = now()
            set_clause = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE knowledge_items SET {set_clause} WHERE id=?",
                        (*fields.values(), item_id))
            con.commit()
        return {"item": _item_row(con, item_id)}
    finally:
        con.close()


@router.delete("/knowledge/{item_id}")
def delete_knowledge(item_id: str):
    con = get_db()
    try:
        con.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


def _snippet(text: str, q: str, width: int = 60) -> str:
    if not text:
        return ""
    idx = text.lower().find(q.lower())
    if idx < 0:
        return text[:width].strip()
    start = max(0, idx - width // 2)
    end = min(len(text), start + width)
    start = max(0, end - width)
    return text[start:end].strip()


@router.get("/search")
def search(q: str | None = None):
    q = (q or "").strip()
    if not q:
        return {"results": []}
    con = get_db()
    try:
        like = f"%{q}%"
        results: list[dict] = []

        for r in con.execute(
                "SELECT id, title, one_line_summary FROM sources WHERE title LIKE ? OR one_line_summary LIKE ?",
                (like, like)):
            hay = r["title"] if (r["title"] and q.lower() in r["title"].lower()) else (r["one_line_summary"] or "")
            results.append({"kind": "source", "source_id": r["id"], "source_title": r["title"],
                             "snippet": _snippet(hay, q), "ref_id": r["id"]})

        for r in con.execute(
                """SELECT k.id, k.title, k.content, k.section_key, k.source_id, s.title AS source_title
                   FROM knowledge_items k JOIN sources s ON s.id = k.source_id
                   WHERE k.title LIKE ? OR k.content LIKE ?""", (like, like)):
            hay = r["title"] if (r["title"] and q.lower() in r["title"].lower()) else (r["content"] or "")
            results.append({"kind": "knowledge", "source_id": r["source_id"], "source_title": r["source_title"],
                             "snippet": _snippet(hay, q), "ref_id": r["id"], "section_key": r["section_key"]})

        for r in con.execute(
                """SELECT qs.id, qs.question_text, qs.source_id, s.title AS source_title
                   FROM questions qs JOIN sources s ON s.id = qs.source_id
                   WHERE qs.question_text LIKE ?""", (like,)):
            results.append({"kind": "question", "source_id": r["source_id"], "source_title": r["source_title"],
                             "snippet": _snippet(r["question_text"] or "", q), "ref_id": r["id"]})

        for r in con.execute(
                """SELECT a.id AS answer_id, a.content, qs.source_id AS source_id, s.title AS source_title
                   FROM answers a JOIN questions qs ON qs.id = a.question_id
                   JOIN sources s ON s.id = qs.source_id
                   WHERE a.content LIKE ?""", (like,)):
            results.append({"kind": "answer", "source_id": r["source_id"], "source_title": r["source_title"],
                             "snippet": _snippet(r["content"] or "", q), "ref_id": r["answer_id"]})

        return {"results": results[:50]}
    finally:
        con.close()
