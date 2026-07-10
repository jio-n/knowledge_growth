"""Export endpoints (§18, §19). Format: docs/architecture/export_spec.md."""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from .db import get_db, now, rows_to_dicts
from .export import note_markdown, source_json

router = APIRouter(prefix="/api")


def _safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title or "export").strip(" .") or "export"
    return name[:100]


def _content_disposition(filename: str) -> str:
    ascii_fallback = re.sub(r"[^\x20-\x7e]", "_", filename) or "export.md"
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(filename)}'


@router.get("/sources/{source_id}/export.md")
def export_markdown(source_id: str, download: int = 0):
    con = get_db()
    try:
        row = con.execute("SELECT title FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, "資料が見つかりません")
        md = note_markdown(con, source_id)
    finally:
        con.close()
    headers = {}
    if download:
        filename = _safe_filename(row["title"]) + ".md"
        headers["Content-Disposition"] = _content_disposition(filename)
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8", headers=headers)


@router.get("/sources/{source_id}/export.json")
def export_source_json(source_id: str):
    con = get_db()
    try:
        if not con.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
            raise HTTPException(404, "資料が見つかりません")
        return source_json(con, source_id)
    finally:
        con.close()


@router.get("/export/all.json")
def export_all_json():
    con = get_db()
    try:
        ids = [r["id"] for r in rows_to_dicts(con.execute("SELECT id FROM sources"))]
        return {"exported_at": now(), "sources": [source_json(con, sid) for sid in ids]}
    finally:
        con.close()
