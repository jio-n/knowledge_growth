"""Background generation -> reviewable preview -> atomic existing Brief storage."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from .brief_context import load_document
from .brief_generation import GenerationError, generate, preview_fields
from .brief_store import get_brief, write_fields
from .db import get_db, new_id, now
from .import_bridge.validator import canonical
from .paper_brief import StrictModel
from .routes_ai import local_control

router = APIRouter(prefix='/api/sources', dependencies=[Depends(local_control)])
MAX_JOB_AGE = timedelta(minutes=20)  # > four bounded runtime turns at default settings
PREVIEW_AGE = timedelta(hours=24)


def snapshot(con, source_id):
    source, version, blocks = load_document(con, source_id)
    # Compare content/metadata, not just version IDs, to detect in-place reanalysis.
    identity = {k: source.get(k) for k in ('id', 'type', 'title', 'authors', 'year', 'doi', 'arxiv_id', 'content_hash')}
    digest = hashlib.sha256(canonical([identity, version, blocks]).encode()).hexdigest()
    brief = get_brief(con, source_id)
    return {'document_digest': digest, 'brief_digest': hashlib.sha256(canonical(brief).encode()).hexdigest()}, source, version, blocks, brief


def recover_stale(con):
    cutoff = (datetime.now(timezone.utc) - MAX_JOB_AGE).isoformat(timespec='seconds')
    con.execute("UPDATE paper_brief_generations SET state='failed',error='interrupted',updated_at=? WHERE state='generating' AND updated_at<?", (now(), cutoff))
    con.commit()


def run_generation(job_id, runtime):
    try:
        with closing(get_db()) as con:
            job = con.execute('SELECT * FROM paper_brief_generations WHERE id=?', (job_id,)).fetchone()
            if not job or job['state'] != 'generating': return
            start, source, version, blocks, existing = snapshot(con, job['source_id'])
            if canonical(start) != job['snapshot_json']:
                raise GenerationError('source_changed')
        fields, coverage = generate(runtime, source, version, blocks)
        rows = preview_fields(existing, fields)
        preview = {'fields': rows, 'context_coverage': coverage,
                   'mode': 'initial' if not existing else 'regenerate_preserve_user' if any(r['action'] == 'preserve_user' for r in rows) else 'regenerate',
                   'can_commit': any(r['action'] in ('new_field', 'generated_update') for r in rows)}
        with closing(get_db()) as con:
            con.execute('BEGIN IMMEDIATE')
            current, *_ = snapshot(con, job['source_id'])
            if current != start:
                raise GenerationError('source_changed')
            con.execute("UPDATE paper_brief_generations SET state='preview',preview_json=?,updated_at=? WHERE id=? AND state='generating'",
                        (canonical(preview), now(), job_id))
            con.commit()
    except Exception as error:
        # Never expose arbitrary exceptions: pydantic and runtime diagnostics may
        # contain paper text/model output/credentials. Brief is never written here.
        code = str(error) if isinstance(error, GenerationError) else 'generation_failed'
        with closing(get_db()) as con:
            con.execute("UPDATE paper_brief_generations SET state='failed',error=?,updated_at=? WHERE id=? AND state='generating'", (code, now(), job_id))
            con.commit()


@router.post('/{source_id}/paper-brief/generate', status_code=202)
def start(source_id: str, request: Request, tasks: BackgroundTasks):
    runtime = request.app.state.ai_runtime
    if runtime.status().state != 'ready' or not runtime.capabilities().inference:
        raise HTTPException(503, 'AI未接続')
    with closing(get_db()) as con:
        recover_stale(con)
        con.execute('BEGIN IMMEDIATE')
        try:
            saved, *_ = snapshot(con, source_id)
            token = new_id()
            ts = now()
            con.execute("INSERT INTO paper_brief_generations (id,source_id,state,snapshot_json,created_at,updated_at) VALUES (?,?,'generating',?,?,?)",
                        (token, source_id, canonical(saved), ts, ts))
            con.commit()
        except ValueError:
            con.rollback()
            raise HTTPException(422, 'registered PDF with text blocks required') from None
        except sqlite3.IntegrityError:
            con.rollback()
            raise HTTPException(409, 'Paper Brief generation already running') from None
    tasks.add_task(run_generation, token, runtime)
    return {'generation_id': token, 'state': 'generating'}


@router.get('/{source_id}/paper-brief/generation')
def state(source_id: str):
    with closing(get_db()) as con:
        recover_stale(con)
        if not con.execute('SELECT 1 FROM sources WHERE id=?', (source_id,)).fetchone():
            raise HTTPException(404, 'source not found')
        row = con.execute('SELECT * FROM paper_brief_generations WHERE source_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (source_id,)).fetchone()
        if not row: return {'state': 'not_generated'}
        preview = json.loads(row['preview_json']) if row['preview_json'] else None
        return {'generation_id': row['id'], 'state': row['state'], 'error': row['error'],
                'preview': preview, 'updated_at': row['updated_at']}


class Confirm(StrictModel):
    generation_id: str
    confirmed: bool


@router.post('/{source_id}/paper-brief/generation/commit')
def commit(source_id: str, body: Confirm):
    if not body.confirmed:
        raise HTTPException(422, 'explicit confirmation required')
    with closing(get_db()) as con:
        con.execute('BEGIN IMMEDIATE')
        try:
            row = con.execute('SELECT * FROM paper_brief_generations WHERE id=? AND source_id=?', (body.generation_id, source_id)).fetchone()
            if not row or row['state'] != 'preview':
                raise HTTPException(409, 'generation preview unavailable or already committed')
            if datetime.fromisoformat(row['updated_at']) + PREVIEW_AGE < datetime.now(timezone.utc):
                raise HTTPException(409, 'generation preview expired; regenerate')
            fresh, *_ = snapshot(con, source_id)
            if canonical(fresh) != row['snapshot_json']:
                raise HTTPException(409, 'stale generation preview; regenerate')
            preview = json.loads(row['preview_json'])
            writes = {f['name']: f['incoming'] for f in preview['fields'] if f['action'] in ('new_field', 'generated_update')}
            if not writes:
                raise HTTPException(409, 'no changed writable fields')
            # Dedicated persistence also enforces origin=user/user_edited protection.
            written = write_fields(con, source_id, writes, automatic=True)
            con.execute("UPDATE paper_brief_generations SET state='completed',updated_at=? WHERE id=?", (now(), row['id']))
            con.commit()
            return {'written_fields': written, 'paper_brief': get_brief(con, source_id)}
        except Exception:
            con.rollback()
            raise
