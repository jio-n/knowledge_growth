"""Source registration / library / document endpoints."""
from __future__ import annotations

import json

from fastapi import Request, APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .analysis import run_analysis_async
from .config import FILES_DIR
from .db import get_db, new_id, now, rows_to_dicts
from .ingest.common import ExtractedDoc, block_ids, doc_text, find_duplicates, sha256_bytes, sha256_text
from .ingest.pdf import extract_pdf
from .ingest.textfile import extract_text
from .ingest.web import extract_web, fetch_url

router = APIRouter(prefix="/api")


def _schedule_analysis(source_id, runtime):
    # Keep the baseline single-argument mock hook used by offline tests/tools.
    if runtime is None or runtime.status().runtime == "mock":
        run_analysis_async(source_id)
    else:
        run_analysis_async(source_id, runtime)


def _insert_source(con, doc: ExtractedDoc, *, type_: str, url: str | None = None,
                   content_hash: str, file_path: str | None = None,
                   raw_path: str | None = None, runtime=None) -> dict:
    sid, vid, ts = new_id(), new_id(), now()
    con.execute(
        """INSERT INTO sources (id, type, title, authors, year, venue, url, canonical_url, doi,
           arxiv_id, site_name, published_at, lang, content_hash, file_path, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sid, type_, doc.title, json.dumps(doc.authors, ensure_ascii=False), doc.year, None,
         url, doc.canonical_url, doc.doi, doc.arxiv_id, doc.site_name, doc.published_at,
         doc.lang, content_hash, file_path, ts, ts))
    con.execute(
        "INSERT INTO source_versions (id, source_id, fetched_at, content_hash, raw_path) VALUES (?,?,?,?,?)",
        (vid, sid, ts, content_hash, raw_path))
    con.executemany(
        """INSERT INTO document_blocks (id, version_id, source_id, idx, kind, level, text, page, heading_path, bbox_json, role)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(bid, vid, sid, i, b.kind, b.level, b.text, b.page, b.heading_path,
          json.dumps(b.bbox) if b.bbox else None, b.role)
         for i, (bid, b) in enumerate(zip(block_ids(vid, doc.blocks), doc.blocks))])
    con.commit()
    _schedule_analysis(sid, runtime)
    return dict(con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone())


def _dup_response(existing: list[dict]) -> dict:
    return {"duplicate": True,
            "existing": [{"id": e["id"], "title": e["title"], "type": e["type"],
                          "created_at": e["created_at"]} for e in existing]}


@router.post("/sources/pdf")
async def register_pdf(request: Request, file: UploadFile = File(...), force: bool = Form(False)):
    data = await file.read()
    if not data[:5] == b"%PDF-":
        raise HTTPException(400, "PDFファイルではありません")
    h = sha256_bytes(data)
    con = get_db()
    try:
        doc = extract_pdf(data)
        dups = find_duplicates(con, content_hash=h, doi=doc.doi, arxiv_id=doc.arxiv_id)
        if dups and not force:
            return _dup_response(dups)
        rel = f"{h[:16]}.pdf"
        (FILES_DIR / rel).write_bytes(data)
        if not doc.title or doc.title == "Untitled PDF":
            doc.title = (file.filename or "Untitled PDF").rsplit(".", 1)[0]
        return {"source": _insert_source(con, doc, runtime=request.app.state.ai_runtime, type_="pdf", content_hash=h,
                                         file_path=rel, raw_path=rel)}
    finally:
        con.close()


class UrlIn(BaseModel):
    url: str
    force: bool = False


@router.post("/sources/url")
def register_url(body: UrlIn, request: Request):
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "http(s) のURLを指定してください")
    # arXiv abs/pdf URL -> normalize to abs page (HTML metadata is richer)
    try:
        final_url, html = fetch_url(url)
    except Exception as e:
        raise HTTPException(502, f"URLの取得に失敗しました: {e}")
    doc = extract_web(final_url, html)
    text_hash = sha256_text(doc_text(doc.blocks))
    con = get_db()
    try:
        dups = find_duplicates(con, content_hash=text_hash, doi=doc.doi,
                               arxiv_id=doc.arxiv_id, url=url, canonical_url=doc.canonical_url)
        if dups and not body.force:
            return _dup_response(dups)
        rel = f"{sha256_text(final_url)[:16]}_{now()[:10]}.html"
        (FILES_DIR / rel).write_text(html, encoding="utf-8")
        return {"source": _insert_source(con, doc, runtime=request.app.state.ai_runtime, type_="web", url=final_url,
                                         content_hash=text_hash, raw_path=rel)}
    finally:
        con.close()


class TextIn(BaseModel):
    content: str
    title: str | None = None
    filename: str | None = None
    force: bool = False


@router.post("/sources/text")
def register_text(body: TextIn, request: Request):
    if not body.content.strip():
        raise HTTPException(400, "本文が空です")
    doc = extract_text(body.content, filename=body.filename)
    if body.title:
        doc.title = body.title
    h = sha256_text(body.content)
    con = get_db()
    try:
        dups = find_duplicates(con, content_hash=h)
        if dups and not body.force:
            return _dup_response(dups)
        type_ = "markdown" if (body.filename or "").endswith((".md", ".markdown")) else "text"
        return {"source": _insert_source(con, doc, runtime=request.app.state.ai_runtime, type_=type_, content_hash=h)}
    finally:
        con.close()


@router.get("/sources")
def list_sources(q: str | None = None, status: str | None = None, tag: str | None = None):
    con = get_db()
    try:
        sql = """SELECT s.*,
            (SELECT COUNT(*) FROM knowledge_items k WHERE k.source_id=s.id AND k.origin != 'auto_extract') AS knowledge_count,
            (SELECT COUNT(*) FROM knowledge_items k WHERE k.source_id=s.id AND k.info_type='open_question') AS open_question_count,
            (SELECT COUNT(*) FROM questions qs WHERE qs.source_id=s.id) AS question_count
            FROM sources s WHERE 1=1"""
        params: list = []
        if q:
            sql += " AND (s.title LIKE ? OR s.authors LIKE ? OR s.one_line_summary LIKE ?)"
            params += [f"%{q}%"] * 3
        if status:
            sql += " AND s.reading_status = ?"
            params.append(status)
        if tag:
            sql += """ AND s.id IN (SELECT st.source_id FROM source_tags st
                       JOIN tags t ON t.id=st.tag_id WHERE t.name = ?)"""
            params.append(tag)
        sql += " ORDER BY COALESCE(s.last_opened_at, s.updated_at) DESC"
        sources = rows_to_dicts(con.execute(sql, params))
        for s in sources:
            s["tags"] = [r["name"] for r in con.execute(
                "SELECT t.name FROM tags t JOIN source_tags st ON st.tag_id=t.id WHERE st.source_id=?",
                (s["id"],))]
        return {"sources": sources}
    finally:
        con.close()


@router.get("/sources/{source_id}")
def get_source(source_id: str):
    con = get_db()
    try:
        row = con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, "資料が見つかりません")
        con.execute("UPDATE sources SET last_opened_at=? WHERE id=?", (now(), source_id))
        con.commit()
        src = dict(row)
        src["tags"] = [r["name"] for r in con.execute(
            "SELECT t.name FROM tags t JOIN source_tags st ON st.tag_id=t.id WHERE st.source_id=?",
            (source_id,))]
        return {"source": src}
    finally:
        con.close()


@router.get("/sources/{source_id}/document")
def get_document(source_id: str):
    con = get_db()
    try:
        version = con.execute(
            "SELECT * FROM source_versions WHERE source_id=? ORDER BY fetched_at DESC LIMIT 1",
            (source_id,)).fetchone()
        if not version:
            raise HTTPException(404, "資料が見つかりません")
        blocks = rows_to_dicts(con.execute(
            "SELECT * FROM document_blocks WHERE version_id=? ORDER BY idx", (version["id"],)))
        for block in blocks:
            block["bbox"] = json.loads(block["bbox_json"]) if block["bbox_json"] else None
        translations = {r["block_id"]: r["translated_text"] for r in con.execute(
            "SELECT block_id, translated_text FROM translations WHERE source_id=? AND block_id IS NOT NULL",
            (source_id,))}
        highlights = rows_to_dicts(con.execute(
            "SELECT * FROM highlights WHERE source_id=?", (source_id,)))
        return {"version": dict(version), "blocks": blocks,
                "translations": translations, "highlights": highlights}
    finally:
        con.close()


@router.get("/sources/{source_id}/file")
def get_file(source_id: str):
    con = get_db()
    try:
        row = con.execute("SELECT file_path, type FROM sources WHERE id=?", (source_id,)).fetchone()
    finally:
        con.close()
    if not row or not row["file_path"]:
        raise HTTPException(404, "元ファイルがありません")
    path = FILES_DIR / row["file_path"]
    if not path.exists():
        raise HTTPException(404, "ファイルが失われています")
    media = "application/pdf" if row["type"] == "pdf" else "text/html"
    return FileResponse(path, media_type=media)


class SourcePatch(BaseModel):
    title: str | None = None
    reading_status: str | None = None
    importance: int | None = None
    tags: list[str] | None = None


@router.patch("/sources/{source_id}")
def patch_source(source_id: str, body: SourcePatch):
    con = get_db()
    try:
        if not con.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
            raise HTTPException(404, "資料が見つかりません")
        for field in ("title", "reading_status", "importance"):
            val = getattr(body, field)
            if val is not None:
                con.execute(f"UPDATE sources SET {field}=?, updated_at=? WHERE id=?",
                            (val, now(), source_id))
        if body.tags is not None:
            con.execute("DELETE FROM source_tags WHERE source_id=?", (source_id,))
            for name in body.tags:
                name = name.strip()
                if not name:
                    continue
                tag = con.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
                tid = tag["id"] if tag else new_id()
                if not tag:
                    con.execute("INSERT INTO tags (id, name) VALUES (?,?)", (tid, name))
                con.execute("INSERT OR IGNORE INTO source_tags (source_id, tag_id) VALUES (?,?)",
                            (source_id, tid))
        con.commit()
        return get_source(source_id)
    finally:
        con.close()


@router.delete("/sources/{source_id}")
def delete_source(source_id: str):
    con = get_db()
    try:
        con.execute("DELETE FROM sources WHERE id=?", (source_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.post("/sources/{source_id}/reanalyze")
def reanalyze(source_id: str, request: Request):
    con = get_db()
    try:
        if not con.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone():
            raise HTTPException(404, "資料が見つかりません")
        con.execute("UPDATE sources SET analysis_status='pending' WHERE id=?", (source_id,))
        con.commit()
    finally:
        con.close()
    _schedule_analysis(source_id, request.app.state.ai_runtime)
    return {"ok": True}
