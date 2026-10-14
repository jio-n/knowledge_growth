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
from .japanese_translation import PDFError, save_unit, translate_units, translation_diagnostics
from .japanese_visual_quality import VisualQualityError, VISUAL_QUALITY_VERSION
from .japanese_semantics import ASSEMBLY_VERSION,report_units,semantic_report,coverage,reading_quality
from .japanese_overlap import content_report,OVERLAP_VERSION
from .japanese_textflow import CLASSIFICATION_VERSION
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


def run_generation(token, runtime, retry_unit_ids=None):
    units = []
    translation_stats = {}
    failure_stage = 'source_validation'
    temporary = output_path(token).with_suffix('.tmp.pdf')
    final = output_path(token)
    source = version = None
    previous_checks={}
    quality_validated=False
    try:
        with closing(get_db()) as con:
            job = con.execute('SELECT * FROM japanese_pdf_generations WHERE id=?', (token,)).fetchone()
            if not job or job['state'] != 'generating': return
            saved = snapshot(con, job['source_id'])
            if not matches(job, saved): raise PDFError('source_changed')
            source, version, blocks, original, _ = saved
            if retry_unit_ids is not None:
                prior=con.execute('SELECT * FROM japanese_pdf_generations WHERE source_id=? AND pages_json=? AND id<>? ORDER BY rowid DESC LIMIT 1',
                                  (source['id'],job['pages_json'],token)).fetchone()
                if prior and matches(prior,saved):previous_checks={u['id']:u.get('translation_validation',[]) for u in json.loads(prior['report_json'] or '{}').get('semantic_units',[])}
        with fitz.open(stream=original, filetype='pdf') as document:
            failure_stage = 'content_detection'
            units = detect_units(document, blocks, json.loads(job['pages_json']))
        content_error=next((u['content_error'] for u in units if u.get('content_error')),None)
        if content_error:raise PDFError(content_error)
        failure_stage = 'translation'
        translate_units(runtime, source, version, units, stats=translation_stats,retry_unit_ids=retry_unit_ids)
        for unit in units:
            if unit.get('retry_deferred'):unit['translation_validation']=previous_checks.get(unit['block']['id'],[])[-8:]
        if not any(u.get('translation_succeeded') for u in units):
            if not any(u.get('translation_candidate') for u in units): failure_stage = 'content_detection'
            raise PDFError('no_safe_translated_blocks')
        failure_stage = 'pdf_layout'
        final.parent.mkdir(parents=True, exist_ok=True)
        report = generate_pdf(original, units, temporary)
        quality_validated=True
        failure_stage = 'publication'
        # Windows os.fsync uses _commit / FlushFileBuffers, which requires write access.
        # Read-only 'rb' succeeds on POSIX but can fail with PermissionError on Windows.
        with temporary.open('r+b') as file:
            os.fsync(file.fileno())
        file_hash = digest(temporary.read_bytes())
        with closing(get_db()) as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT * FROM japanese_pdf_generations WHERE id=?', (token,)).fetchone()
            if not current or current['state'] != 'generating' or not matches(job, snapshot(con, source['id'])):
                raise PDFError('source_changed')
            for unit in units:
                unit['_publication_state']='published'
                if not unit.get('translation_succeeded') and not unit.get('retry_deferred'):save_unit(con, source, version, unit)
            report['units']=report_units(units)
            report['semantic_units']=semantic_report(units)
            report['diagnostics'] = translation_diagnostics(units, translation_stats, published=True)
            os.replace(temporary, final)  # same filesystem; DB never exposes a partial PDF
            con.execute("UPDATE japanese_pdf_generations SET state='preview_ready',file_hash=?,report_json=?,updated_at=? WHERE id=?",
                        (file_hash, canonical(report), now(), token))
            con.commit()
    except Exception as error:
        code = str(error) if isinstance(error, PDFError) else 'generation_failed'
        final.unlink(missing_ok=True)
        # AI success was committed before layout. A failure to publish a PDF
        # must never rewrite that row (or a user edit) as a translation failure.
        for unit in units:unit['_publication_state']='failed'
        with closing(get_db()) as con:
            job = con.execute('SELECT 1 FROM japanese_pdf_generations WHERE id=?', (token,)).fetchone()
            if job:
                if failure_stage!='content_detection' and source and version and con.execute('SELECT 1 FROM source_versions WHERE id=? AND source_id=?', (version['id'], source['id'])).fetchone():
                    for unit in units:
                        if not unit.get('translation_succeeded') and not unit.get('cached') and not unit.get('retry_deferred'):
                            save_unit(con, source, version, unit)
                report = {'classification_version':CLASSIFICATION_VERSION,'layout_version':LAYOUT_VERSION,'assembly_version':ASSEMBLY_VERSION,'counts': dict(Counter(u['status'] for u in units)),
                          'units':report_units(units),'semantic_units':semantic_report(units),'coverage':coverage(units),
                          'overlap_version':OVERLAP_VERSION,'content_detection':content_report(units),
                          'reading_quality':reading_quality(units,quality_validated=quality_validated)}
                report['diagnostics'] = translation_diagnostics(units, translation_stats, failure_stage=failure_stage,quality_validated=quality_validated)
                if isinstance(error, VisualQualityError):
                    report['quality'] = error.quality
                    report['diagnostics']['quality_validation_state']='failed'
                    by_asset={row['asset_id']:row for row in error.quality['visuals']}
                    for unit in units:
                        if unit['kind']=='visual' and unit['asset_id'] in by_asset:
                            q=by_asset[unit['asset_id']]
                            unit.update(_visual_quality_state=q['status'],_visual_quality_error=q['error'])
                    report['units']=report_units(units)
                    report['semantic_units']=semantic_report(units)
                    report['diagnostics']['visual_failures'] = dict(Counter(
                        'annotated_page' if row['annotations_present'] else row['asset_kind']
                        for row in error.quality['visuals'] if row['status']=='failed'))
                con.execute("UPDATE japanese_pdf_generations SET state='failed',error=?,report_json=?,updated_at=? WHERE id=? AND state='generating'",
                            (code, canonical(report), now(), token))
                con.commit()
    finally:
        temporary.unlink(missing_ok=True)


class GenerateIn(StrictModel):
    pages: list[Annotated[int, Field(strict=True, ge=1)]] = Field(min_length=1, max_length=3)
    retry_failed: bool = False
    retry_unit_ids: list[Annotated[str,Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9]+$')]] | None = Field(default=None,min_length=1,max_length=160)


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
            if body.retry_unit_ids is not None:
                if not body.retry_failed or len(set(body.retry_unit_ids))!=len(body.retry_unit_ids):
                    raise HTTPException(422,'対象単位の再試行指定が不正です')
                # A retry can only target failed semantic units belonging to a
                # current-source job for these selected pages, never a source ID
                # from another version/artifact or an already successful edit.
                previous=con.execute('SELECT * FROM japanese_pdf_generations WHERE source_id=? AND pages_json=? ORDER BY rowid DESC LIMIT 1',(source_id,pages)).fetchone()
                prior=json.loads(previous['report_json'] or '{}') if previous and matches(previous,saved) else {}
                eligible={u['id'] for u in prior.get('semantic_units',[]) if u.get('ai_translation_state')=='failed'} if prior.get('layout_version')==LAYOUT_VERSION else set()
                if not set(body.retry_unit_ids)<=eligible:raise HTTPException(422,'現在の生成結果で翻訳未成功の意味単位を指定してください')
            active = con.execute("SELECT * FROM japanese_pdf_generations WHERE source_id=? AND state='generating'", (source_id,)).fetchone()
            if active:
                if active['pages_json'] == pages and matches(active, saved): return public(active)
                raise HTTPException(409, '日本語PDFを生成中です。完了後に再試行してください')
            ready = con.execute("SELECT * FROM japanese_pdf_generations WHERE source_id=? AND pages_json=? AND state='preview_ready' ORDER BY rowid DESC", (source_id, pages)).fetchall()
            if not body.retry_failed:
                for row in ready:
                    path = output_path(row['id'])
                    report = json.loads(row['report_json'] or '{}')
                    if (report.get('layout_version') == LAYOUT_VERSION and report.get('assembly_version') == ASSEMBLY_VERSION and report.get('visual_quality_version') == VISUAL_QUALITY_VERSION
                            and matches(row, saved) and path.is_file() and digest(path.read_bytes()) == row['file_hash']):
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
    tasks.add_task(run_generation, token, runtime, body.retry_unit_ids)
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
