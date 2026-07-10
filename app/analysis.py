"""Initial structured extraction pipeline (§4, §12).

Runs after ingest (in a background thread — registration returns
immediately and the client polls analysis_status). Results are stored as
knowledge_items with origin='auto_extract' so they are always
distinguishable from what the user saved or wrote (§13).

If the LLM returns unusable JSON — or the provider is the offline mock —
we fall back to a heuristic extraction so the workspace is still useful
without any API key.
"""
from __future__ import annotations

import json
import threading
import traceback

from . import context as ctx
from .db import get_db, new_id, now
from .llm import get_provider
from .llm.base import strip_json_fences
from .prompts import get_prompt

SECTION_FOR_FIELD = {
    "background": "background",
    "method": "method",
    "experiments": "experiments",
    "novelty": "novelty",
    "limitations": "limitations",
}
INFO_TYPE_FOR_FIELD = {
    "background": "llm_summary",
    "method": "llm_summary",
    "experiments": "llm_summary",
    "novelty": "llm_summary",
    "limitations": "llm_summary",
}


def run_analysis_async(source_id: str) -> None:
    threading.Thread(target=run_analysis, args=(source_id,), daemon=True).start()


def run_analysis(source_id: str) -> None:
    con = get_db()
    try:
        con.execute("UPDATE sources SET analysis_status='running', updated_at=? WHERE id=?",
                    (now(), source_id))
        con.commit()
        src = con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        excerpt = ctx.doc_excerpt(con, source_id)

        data: dict = {}
        provider_name, model = "heuristic", "heuristic"
        try:
            prompt = get_prompt("initial_extraction")
            provider = get_provider()
            res = provider.complete_json(
                "あなたは研究資料から構造化情報を抽出するアシスタントです。",
                prompt.render(title=src["title"], doc_excerpt=excerpt),
                max_tokens=3000,
            )
            data = json.loads(strip_json_fences(res.text))
            provider_name, model = res.provider, res.model
        except Exception:
            data = {}

        if not data or not data.get("one_line_summary"):
            data = _heuristic_extraction(con, source_id, src["title"])

        _store_extraction(con, source_id, data, provider_name, model)
        con.execute("UPDATE sources SET analysis_status='done', one_line_summary=?, updated_at=? WHERE id=?",
                    (data.get("one_line_summary"), now(), source_id))
        con.commit()
    except Exception as e:
        traceback.print_exc()
        con.execute("UPDATE sources SET analysis_status='error', analysis_error=?, updated_at=? WHERE id=?",
                    (str(e)[:500], now(), source_id))
        con.commit()
    finally:
        con.close()


def _store_extraction(con, source_id: str, data: dict, provider: str, model: str) -> None:
    # re-analysis replaces previous auto-extracted items, never user items (§21)
    con.execute("DELETE FROM knowledge_items WHERE source_id=? AND origin='auto_extract'", (source_id,))
    order = 0.0

    def add(section: str, info_type: str, title: str | None, content: str):
        nonlocal order
        order += 1
        con.execute(
            """INSERT INTO knowledge_items
               (id, source_id, section_key, title, content, origin, info_type, created_at, updated_at, sort_order)
               VALUES (?,?,?,?,?,'auto_extract',?,?,?,?)""",
            (new_id(), source_id, section, title, content, info_type, now(), now(), order))

    for field, section in SECTION_FOR_FIELD.items():
        val = data.get(field)
        if val:
            add(section, INFO_TYPE_FOR_FIELD[field], None, str(val))
    for t in data.get("terms") or []:
        if isinstance(t, dict) and t.get("term"):
            add("terms", "term", t["term"], t.get("definition", ""))
    for q in data.get("open_questions") or []:
        if q:
            add("questions", "open_question", None, str(q))
    con.commit()


def _heuristic_extraction(con, source_id: str, title: str) -> dict:
    """No-LLM fallback: abstract-ish first paragraphs + heading outline."""
    rows = con.execute(
        "SELECT kind, text, heading_path FROM document_blocks WHERE source_id=? ORDER BY idx LIMIT 200",
        (source_id,),
    ).fetchall()
    paras = [r["text"] for r in rows if r["kind"] == "para" and len(r["text"]) > 80]
    headings = [r["text"] for r in rows if r["kind"] == "heading"]
    summary = (paras[0][:150] + "…") if paras else title
    outline = " / ".join(headings[:12]) if headings else None
    return {
        "one_line_summary": summary,
        "background": paras[0] if paras else None,
        "method": ("見出し構成: " + outline) if outline else None,
        "experiments": None,
        "novelty": None,
        "limitations": None,
        "terms": [],
        "open_questions": ["(自動抽出はヒューリスティックで実行されました。LLMプロバイダーを設定すると精度が向上します)"],
    }
