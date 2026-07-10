"""Question / answer / translation endpoints (§9, §10, §15)."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import context as ctx
from .db import get_db, new_id, now, rows_to_dicts
from .llm import get_provider
from .llm.base import LLMError
from .prompts import get_prompt

router = APIRouter(prefix="/api")


class QuestionIn(BaseModel):
    anchor: dict | None = None          # SourceAnchor (source_anchor_spec.md)
    selection_text: str | None = None
    prompt_type: str = "free"           # explain | explain_simple | detail | critique | compare | apply | free
    question_text: str


PROMPT_TYPE_PREFIX = {
    "explain": "次の選択箇所を分かりやすく説明してください。",
    "explain_simple": "次の選択箇所を、この分野の初学者にも分かるように、前提知識から説明してください。",
    "detail": "次の選択箇所を、専門家向けにより詳しく、技術的な深さをもって説明してください。",
    "critique": "次の選択箇所の主張を批判的に検討してください。根拠は十分か、暗黙の前提は何か、弱点はどこかを分析してください。",
    "apply": "次の選択箇所の内容を、ユーザー自身の研究や実装へ応用する方法を考えてください。具体的な実験案・実装案も挙げてください。",
    "math": "次の数式・記号を含む選択箇所を、各項の意味に分解して説明してください。",
}


@router.post("/sources/{source_id}/questions")
def ask_question(source_id: str, body: QuestionIn):
    con = get_db()
    try:
        src = con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not src:
            raise HTTPException(404, "資料が見つかりません")

        block_idx = (body.anchor or {}).get("blockIdx")
        before, after, heading = ctx.surrounding_context(con, source_id, block_idx)
        if not heading:
            heading = (body.anchor or {}).get("headingPath", "") or "(位置不明)"
        digest = ctx.note_digest(con, source_id)

        question_full = body.question_text
        prefix = PROMPT_TYPE_PREFIX.get(body.prompt_type)
        if prefix and body.question_text.strip() in ("", body.prompt_type):
            question_full = prefix

        prompt = get_prompt("answer_question")
        user_prompt = prompt.render(
            title=src["title"], heading_path=heading,
            context_before=before or "(なし)", context_after=after or "(なし)",
            selection=body.selection_text or "(選択なし — 資料全体への質問)",
            note_digest=digest, question=question_full)

        try:
            provider = get_provider()
            res = provider.complete(
                "あなたは研究資料の読解を支援する誠実なアシスタントです。",
                user_prompt, hint="answer")
        except LLMError as e:
            raise HTTPException(502, str(e))

        qid, aid, ts = new_id(), new_id(), now()
        con.execute(
            """INSERT INTO questions (id, source_id, anchor, selection_text, prompt_type, question_text, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (qid, source_id, json.dumps(body.anchor, ensure_ascii=False) if body.anchor else None,
             body.selection_text, body.prompt_type, question_full, ts))
        con.execute(
            """INSERT INTO answers (id, question_id, content, provider, model, prompt_id, prompt_version,
               context_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (aid, qid, res.text, res.provider, res.model, prompt.id, prompt.version,
             ctx.context_record(heading=heading, selection_chars=len(body.selection_text or ""),
                                before_chars=len(before), after_chars=len(after),
                                note_digest_used=digest != "(まだノートはありません)"),
             ts))
        con.commit()
        return {"question": _question_row(con, qid)}
    finally:
        con.close()


@router.get("/sources/{source_id}/questions")
def list_questions(source_id: str):
    con = get_db()
    try:
        rows = con.execute("SELECT id FROM questions WHERE source_id=? ORDER BY created_at",
                           (source_id,)).fetchall()
        return {"questions": [_question_row(con, r["id"]) for r in rows]}
    finally:
        con.close()


def _question_row(con, qid: str) -> dict:
    q = dict(con.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone())
    q["anchor"] = json.loads(q["anchor"]) if q["anchor"] else None
    q["answers"] = rows_to_dicts(con.execute(
        "SELECT * FROM answers WHERE question_id=? ORDER BY created_at", (qid,)))
    # which answers already have saved knowledge items
    saved = {r["answer_id"] for r in con.execute(
        "SELECT DISTINCT answer_id FROM knowledge_items WHERE question_id=?", (qid,))}
    for a in q["answers"]:
        a["saved"] = a["id"] in saved
    return q


class TranslateIn(BaseModel):
    source_id: str
    text: str
    block_id: str | None = None


@router.post("/translate")
def translate(body: TranslateIn):
    con = get_db()
    try:
        if body.block_id:
            existing = con.execute(
                "SELECT translated_text FROM translations WHERE source_id=? AND block_id=?",
                (body.source_id, body.block_id)).fetchone()
            if existing:
                return {"translation": existing["translated_text"], "cached": True}
        prompt = get_prompt("translate")
        try:
            res = get_provider().complete(
                "あなたは学術文書の翻訳者です。", prompt.render(selection=body.text),
                hint="translate")
        except LLMError as e:
            raise HTTPException(502, str(e))
        if body.block_id:
            version = con.execute(
                "SELECT version_id FROM document_blocks WHERE id=?", (body.block_id,)).fetchone()
            con.execute(
                """INSERT INTO translations (id, source_id, version_id, block_id, source_text,
                   translated_text, provider, model, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (new_id(), body.source_id, version["version_id"] if version else None,
                 body.block_id, body.text, res.text, res.provider, res.model, now()))
            con.commit()
        return {"translation": res.text, "cached": False}
    finally:
        con.close()


class HighlightIn(BaseModel):
    source_id: str
    anchor: dict
    color: str = "yellow"
    comment: str | None = None


@router.post("/highlights")
def add_highlight(body: HighlightIn):
    con = get_db()
    try:
        hid = new_id()
        con.execute(
            "INSERT INTO highlights (id, source_id, anchor, color, comment, created_at) VALUES (?,?,?,?,?,?)",
            (hid, body.source_id, json.dumps(body.anchor, ensure_ascii=False), body.color,
             body.comment, now()))
        con.commit()
        return {"id": hid}
    finally:
        con.close()


@router.delete("/highlights/{highlight_id}")
def delete_highlight(highlight_id: str):
    con = get_db()
    try:
        con.execute("DELETE FROM highlights WHERE id=?", (highlight_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()
