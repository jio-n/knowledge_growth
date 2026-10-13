"""Registered-PDF jobs with hash/version checks and atomic derived-file publication."""
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
import re
from typing import Annotated
import fitz
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field
from .config import files_dir, generated_dir
from .db import get_db, new_id, now
from .import_bridge.validator import canonical
from .japanese_pdf import detect_units, generate_pdf, LAYOUT_VERSION
from .japanese_translation import PDFError, save_unit, translate_units
from .paper_brief import StrictModel
from .routes_ai import local_control

router = APIRouter(prefix='/api/sources', dependencies=[Depends(local_control)])


def digest(data): return hashlib.sha256(data).hexdigest()


def snapshot(con, source_id):
    source = con.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
    if not source: raise HTTPException(404, '資料が見つかりません')
    if source['type'] != 'pdf': raise HTTPException(422, '登録済みPDFが必要です')
    version = con.execute('SELECT * FROM source_versions WHERE source_id=? ORDER BY fetched_at DESC,rowid DESC LIMIT 1', (source_id,)).fetchone()
    if not version: raise PDFError('source_version_missing')
    root = files_dir().resolve()
    path = (root / (version['raw_path'] or '')).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise PDFError('source_file_missing')
    data = path.read_bytes()
    if digest(data) != source['content_hash'] or digest(data) != version['content_hash']:
        raise PDFError('source_hash_changed')
    blocks = [dict(b) for b in con.execute('SELECT * FROM document_blocks WHERE source_id=? AND version_id=? ORDER BY idx', (source_id, version['id']))]
    for b in blocks:
        b['bbox'] = json.loads(b['bbox_json']) if b['bbox_json'] else None
    document_digest = digest(canonical([version['id'], version['content_hash'], blocks]).encode())
    return dict(source), dict(version), blocks, data, document_digest


def output_path(token):
    if not re.fullmatch(r'[a-f0-9]{16}', token): raise HTTPException(404, '生成PDFが見つかりません')
    root = generated_dir().resolve()
    path = (root / f'{token}.pdf').resolve()
    if not path.is_relative_to(root): raise HTTPException(404, '生成PDFが見つかりません')
    return path


def matches(job, snapshot_result):
    _, version, _, _, document_digest = snapshot_result
    return (job['source_version_id'] == version['id'] and job['source_hash'] == version['content_hash']
            and job['document_digest'] == document_digest)


def public(job):
    return {'generation_id': job['id'], 'state': job['state'], 'pages': json.loads(job['pages_json']),
            'report': json.loads(job['report_json']) if job['report_json'] else None,
            'error': job['error'], 'updated_at': job['updated_at']}


def run_generation(token, runtime):
    units = []
    temporary = output_path(token).with_suffix('.tmp.pdf')
    final = output_path(token)
    source = version = None
    try:
        with closing(get_db()) as con:
            job = con.execute('SELECT * FROM japanese_pdf_generations WHERE id=?', (token,)).fetchone()
            if not job or job['state'] != 'generating': return
            saved = snapshot(con, job['source_id'])
            if not matches(job, saved): raise PDFError('source_changed')
            source, version, blocks, original, _ = saved
        with fitz.open(stream=original, filetype='pdf') as document:
            units = detect_units(document, blocks, json.loads(job['pages_json']))
        translate_units(runtime, source, version, units)
        final.parent.mkdir(parents=True, exist_ok=True)
        report = generate_pdf(original, units, temporary)
        with temporary.open('rb') as file:
            os.fsync(file.fileno())
        file_hash = digest(temporary.read_bytes())
        with closing(get_db()) as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT * FROM japanese_pdf_generations WHERE id=?', (token,)).fetchone()
            if not current or current['state'] != 'generating' or not matches(job, snapshot(con, source['id'])):
                raise PDFError('source_changed')
            for unit in units: save_unit(con, source, version, unit)
            for row, unit in zip(report['units'], units): row['translation_id'] = unit['translation_id']
            os.replace(temporary, final)  # same filesystem; DB never exposes a partial PDF
            con.execute("UPDATE japanese_pdf_generations SET state='preview_ready',file_hash=?,report_json=?,updated_at=? WHERE id=?",
                        (file_hash, canonical(report), now(), token))
            con.commit()
    except Exception as error:
        code = str(error) if isinstance(error, PDFError) else 'generation_failed'
        final.unlink(missing_ok=True)
        # Preserve completed AI text for a retry, but never mark unsafe placement translated.
        with closing(get_db()) as con:
            job = con.execute('SELECT 1 FROM japanese_pdf_generations WHERE id=?', (token,)).fetchone()
            if job:
                if source and version and con.execute('SELECT 1 FROM source_versions WHERE id=? AND source_id=?', (version['id'], source['id'])).fetchone():
                    for unit in units:
                        if unit['status'] in ('pending', 'translated'):
                            unit.update(status='failed', reason=code)
                        save_unit(con, source, version, unit)
                report = {'counts': dict(Counter(u['status'] for u in units)), 'units': [{'block_id': u['block']['id'], 'page': u['block']['page'],
                          'status': u['status'], 'reason': u['reason']} for u in units]}
                con.execute("UPDATE japanese_pdf_generations SET state='failed',error=?,report_json=?,updated_at=? WHERE id=? AND state='generating'",
                            (code, canonical(report), now(), token))
                con.commit()
    finally:
        temporary.unlink(missing_ok=True)


class GenerateIn(StrictModel):
    pages: list[Annotated[int, Field(strict=True, ge=1)]] = Field(min_length=1, max_length=3)
    retry_failed: bool = False


@router.post('/{source_id}/japanese-pdf/generate', status_code=202)
def start(source_id: str, body: GenerateIn, request: Request, tasks: BackgroundTasks):
    if len(set(body.pages)) != len(body.pages): raise HTTPException(422, 'ページ指定が重複しています')
    pages = canonical(sorted(body.pages))
    with closing(get_db()) as con:
        con.execute('BEGIN IMMEDIATE')
        try:
            saved = snapshot(con, source_id)
            source, version, _, original, document_digest = saved
            with fitz.open(stream=original, filetype='pdf') as doc:
                if max(body.pages) > len(doc): raise HTTPException(422, 'ページ番号が範囲外です')
            active = con.execute("SELECT * FROM japanese_pdf_generations WHERE source_id=? AND state='generating'", (source_id,)).fetchone()
            if active:
                if active['pages_json'] == pages and matches(active, saved): return public(active)
                raise HTTPException(409, '日本語PDFを生成中です。完了後に再試行してください')
            ready = con.execute("SELECT * FROM japanese_pdf_generations WHERE source_id=? AND pages_json=? AND state='preview_ready' ORDER BY rowid DESC", (source_id, pages)).fetchall()
            if not body.retry_failed:
                for row in ready:
                    path = output_path(row['id'])
                    if json.loads(row['report_json'] or '{}').get('layout_version') == LAYOUT_VERSION and matches(row, saved) and path.is_file() and digest(path.read_bytes()) == row['file_hash']:
                        return public(row)
            runtime = request.app.state.ai_runtime
            if runtime.status().state != 'ready' or not runtime.capabilities().inference:
                raise HTTPException(503, 'AI未接続です。ChatGPTで接続してください')
            token, ts = new_id(), now()
            con.execute('''INSERT INTO japanese_pdf_generations(id,source_id,source_version_id,source_hash,
                document_digest,pages_json,state,created_at,updated_at) VALUES (?,?,?,?,?,?,'generating',?,?)''',
                (token, source_id, version['id'], version['content_hash'], document_digest, pages, ts, ts))
            con.commit()
        except PDFError as error:
            raise HTTPException(409, str(error)) from None
    tasks.add_task(run_generation, token, runtime)
    return {'generation_id': token, 'state': 'generating', 'pages': sorted(body.pages)}


@router.get('/{source_id}/japanese-pdf')
def state(source_id: str):
    with closing(get_db()) as con:
        try: saved = snapshot(con, source_id)
        except PDFError as error: return {'state': 'not_generated', 'error': str(error), 'artifacts': []}
        rows = con.execute('SELECT * FROM japanese_pdf_generations WHERE source_id=? ORDER BY rowid DESC', (source_id,)).fetchall()
        artifacts = []
        for row in rows:
            if not matches(row, saved): continue
            item = public(row)
            if row['state'] == 'preview_ready':
                path = output_path(row['id'])
                if not path.is_file() or digest(path.read_bytes()) != row['file_hash']:
                    item.update(state='failed', error='generated_file_missing_or_changed')
            artifacts.append(item)
        return {**(artifacts[0] if artifacts else {'state': 'not_generated'}), 'artifacts': artifacts}


@router.get('/{source_id}/japanese-pdf/{generation_id}/file')
def file(source_id: str, generation_id: str, download: bool = False):
    path = output_path(generation_id)
    with closing(get_db()) as con:
        job = con.execute('SELECT * FROM japanese_pdf_generations WHERE id=? AND source_id=?', (generation_id, source_id)).fetchone()
        if not job or job['state'] != 'preview_ready': raise HTTPException(404, '生成PDFが見つかりません')
        try:
            if not matches(job, snapshot(con, source_id)): raise PDFError('source_changed')
        except PDFError as error: raise HTTPException(409, str(error)) from None
        if not path.is_file() or digest(path.read_bytes()) != job['file_hash']:
            raise HTTPException(409, '生成ファイルが失われたか変更されています。再生成してください')
    return FileResponse(path, media_type='application/pdf', filename=f'japanese-prototype-{generation_id}.pdf' if download else None,
                        headers={'Cache-Control': 'no-store'})
