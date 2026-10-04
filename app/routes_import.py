"""Offline Import Bridge API. Upload parsing and research-data commit are separate."""
import json
from contextlib import closing

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from .brief_store import get_brief, write_fields
from .db import get_db, now
from .paper_brief import FIELD_TYPES, effective_status, nested_evidence
from .source_anchor import resolve_portable_evidence
from .import_bridge.schema import PortableEvidence
from .import_bridge.service import (ImportConflict, PreviewOptions, commit_preview, stage_preview)
from .import_bridge.validator import MAX_PACKAGE_BYTES, PackageError, payload_hash, validate_package

router = APIRouter(prefix='/api')


async def _upload(file):
    data = await file.read(MAX_PACKAGE_BYTES + 1)
    if len(data) > MAX_PACKAGE_BYTES:
        raise HTTPException(413, 'oversized package')
    return data


def _error(exc):
    if isinstance(exc, ImportConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(422, exc.errors(include_input=False, include_url=False, include_context=False))
    return HTTPException(422, str(exc))


@router.post('/import/kgpack/validate')
async def validate(file: UploadFile = File(...)):
    try:
        package = validate_package(await _upload(file))
        return {'valid': True, 'package_id': package.manifest.package_id, 'payload_hash': payload_hash(package),
                'schema_version': package.manifest.kgpack_schema_version}
    except PackageError as exc:
        raise _error(exc) from exc


@router.post('/import/kgpack/preview')
async def preview(file: UploadFile = File(...), source_id: str | None = Form(None), exclude_fields: str = Form('[]')):
    try:
        options = PreviewOptions.model_validate({'source_id': source_id, 'exclude_fields': json.loads(exclude_fields)})
        data = await _upload(file)
        with closing(get_db()) as con:
            return stage_preview(con, data, options)
    except (PackageError, ValidationError, ValueError) as exc:
        raise _error(exc) from exc


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    preview_id: str
    confirmed: bool


@router.post('/import/kgpack/commit')
def commit(body: CommitRequest):
    try:
        with closing(get_db()) as con:
            return commit_preview(con, body.preview_id, confirmed=body.confirmed)
    except (PackageError, ValidationError) as exc:
        raise _error(exc) from exc


@router.get('/sources/{source_id}/paper-brief')
def read_brief(source_id: str):
    with closing(get_db()) as con:
        if not con.execute('SELECT 1 FROM sources WHERE id=?', (source_id,)).fetchone():
            raise HTTPException(404, 'source not found')
        return {'paper_brief': get_brief(con, source_id)}


@router.patch('/sources/{source_id}/paper-brief/fields/{field_name}')
def edit_field(source_id: str, field_name: str, body: dict):
    """An explicit local user edit; imported data cannot call this policy internally."""
    if field_name not in FIELD_TYPES:
        raise HTTPException(422, 'unknown Paper Brief field')
    try:
        field = TypeAdapter(FIELD_TYPES[field_name]).validate_python(body).model_dump(mode='json')
    except ValidationError as exc:
        raise _error(exc) from exc
    with closing(get_db()) as con:
        con.execute('BEGIN IMMEDIATE')
        try:
            if not con.execute('SELECT 1 FROM sources WHERE id=?', (source_id,)).fetchone():
                raise HTTPException(404, 'source not found')
            existing = get_brief(con, source_id)
            current = existing['fields'].get(field_name) if existing else None
            refs = {e['evidence_id']: e for e in current['evidence_resolution']} if current else {}
            # A user edit may reuse references, but their old resolution is not proof
            # against a different current version. Re-resolve without changing history.
            version = con.execute('SELECT * FROM source_versions WHERE source_id=? ORDER BY fetched_at DESC,rowid DESC LIMIT 1', (source_id,)).fetchone()
            blocks = [dict(b) for b in con.execute('SELECT * FROM document_blocks WHERE version_id=? ORDER BY idx', (version['id'],))] if version else []
            for block in blocks:
                block['bbox'] = json.loads(block['bbox_json']) if block['bbox_json'] else None
            refs = {eid: resolve_portable_evidence(PortableEvidence.model_validate(e['portable']), blocks,
                    dict(version) if version else {'id': None},
                    source_hash=(e.get('anchor') or {}).get('sourceHash')) for eid, e in refs.items()}
            required = set(field['evidence'])
            required.update(nested_evidence(field_name, field))
            if field_name == 'key_results':
                results = field['value'] or []
                if len({r['id'] for r in results}) != len(results):
                    raise HTTPException(422, 'duplicate Key Result ID')
                required |= {e for r in results for e in r['evidence']}
            if required - refs.keys():
                raise HTTPException(422, 'user edit may only reuse existing field evidence')
            field['status'] = effective_status(field['status'], list(required), refs)
            if field_name == 'key_results':
                for result in field['value'] or []:
                    result['status'] = effective_status(result['status'], result['evidence'], refs)
                if field['status'] == 'confirmed' and any(r['status'] == 'uncertain' for r in field['value'] or []):
                    field['status'] = 'uncertain'
            field.update(origin='user', user_edited=True, evidence_resolution=[refs[e] for e in sorted(required)])
            field['provenance'] = {**(current['provenance'] if current else {}), 'user_edit': field['provenance'], 'edited_at': now()}
            write_fields(con, source_id, {field_name: field}, automatic=False)
            con.commit()
            return {'paper_brief': get_brief(con, source_id)}
        except Exception:
            con.rollback()
            raise
