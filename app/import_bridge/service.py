"""Preview snapshots and confirmation-based, atomic commit."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated
from pydantic import Field
from app.brief_store import get_brief, write_fields
from app.db import new_id, now
from app.paper_brief import Identifier, StrictModel, brief_fields, effective_status
from app.source_anchor import resolve_portable_evidence
from .matching import local_sources, match_source
from .schema import Package
from .validator import PackageError, canonical, payload_hash, validate_package


class ImportConflict(PackageError):
    pass


class PreviewOptions(StrictModel):
    source_id: Identifier | None = None  # explicit manual selection
    exclude_fields: Annotated[list[str], Field(max_length=128)] = Field(default_factory=list)


def build_preview(con, package: Package, options: PreviewOptions) -> dict:
    fields = brief_fields(package.paper_brief)
    if len(set(options.exclude_fields)) != len(options.exclude_fields) or set(options.exclude_fields) - set(fields):
        raise PackageError('invalid/duplicate excluded field')
    matching = match_source(con, package.manifest.source_identity)
    source_id = options.source_id or matching['source_id']
    target = next((s for s in local_sources(con) if s['id'] == source_id), None)
    if options.source_id and not target:
        raise PackageError('manual target must be an existing PDF source with a version')
    if target:
        version = dict(con.execute('SELECT * FROM source_versions WHERE id=?', (target['version_id'],)).fetchone())
        blocks = [dict(b) for b in con.execute('SELECT * FROM document_blocks WHERE version_id=? ORDER BY idx', (version['id'],))]
        for b in blocks:
            b['bbox'] = json.loads(b['bbox_json']) if b['bbox_json'] else None
        resolved = [resolve_portable_evidence(e, blocks, version, source_hash=package.manifest.source_identity.source_hash)
                    for e in package.evidence_refs]
    else:
        resolved = [{'evidence_id': e.evidence_id, 'portable': e.model_dump(mode='json'),
                     'status': 'unresolved', 'method': None, 'reason': 'source_not_selected',
                     'block': None, 'page': None, 'candidates': [], 'anchor': None} for e in package.evidence_refs]
    resolutions = {r['evidence_id']: r for r in resolved}
    existing = get_brief(con, source_id) if target else None
    previous = existing['fields'] if existing else {}
    preview_fields, conflicts, warnings = [], [], []
    for name, field in fields.items():
        incoming = json.loads(canonical(field))
        requested_status = incoming['status']
        incoming['status'] = effective_status(incoming['status'], incoming['evidence'], resolutions)
        if name == 'key_results':
            for result in incoming['value'] or []:
                requested = result['status']
                result['status'] = effective_status(requested, result['evidence'], resolutions)
                if requested != result['status']:
                    warnings.append({'field': name, 'result_id': result['id'], 'reason': 'numeric_evidence_not_resolved'})
            if any(r['status'] == 'uncertain' for r in incoming['value'] or []) and incoming['status'] == 'confirmed':
                incoming['status'] = 'uncertain'
        if incoming['status'] != requested_status:
            warnings.append({'field': name, 'reason': 'confirmed_requires_resolved_evidence'})
        current = previous.get(name)
        protected = bool(current and (current['origin'] == 'user' or current['user_edited']))
        excluded = name in options.exclude_fields
        action = 'excluded' if excluded else 'preserve_user' if protected else 'update' if current else 'add'
        if current:
            conflicts.append({'field': name, 'type': 'user_edited' if current['user_edited'] else
                              'user_origin' if current['origin'] == 'user' else 'existing_field',
                              'policy': action, 'current': current, 'incoming': incoming})
        preview_fields.append({'name': name, 'action': action, 'incoming': incoming,
            'requested_status': requested_status, 'current': current,
            'evidence_resolution': [resolutions[e] for e in incoming['evidence']],
            'result_evidence': {r['id']: [resolutions[e] for e in r['evidence']]
                                for r in incoming['value'] or []} if name == 'key_results' else {}})
    duplicates = [dict(r) for r in con.execute('SELECT package_id,source_id FROM import_packages WHERE package_id=? OR payload_hash=?',
                                              (package.manifest.package_id, payload_hash(package)))]
    counts = {s: sum(r['status'] == s for r in resolved) for s in ('resolved', 'candidates', 'page_only', 'unresolved')}
    selected_count = sum(f['action'] in ('add', 'update') for f in preview_fields)
    can_commit = bool(target and not duplicates and selected_count)
    review_reasons = []
    if not target:
        review_reasons.append('manual_source_selection_required')
    if counts['candidates'] or counts['unresolved'] or counts['page_only'] or warnings:
        review_reasons.append('evidence_needs_review')
    if conflicts:
        review_reasons.append('field_conflicts')
    if duplicates:
        review_reasons.append('duplicate_import')
    if not selected_count:
        review_reasons.append('no_writable_fields')
    if options.source_id:
        review_reasons.append('manual_source_selection')
    if package.knowledge_items is not None or package.qa_threads is not None:
        review_reasons.append('optional_candidates_archived_only')
    return {'package_id': package.manifest.package_id, 'payload_hash': payload_hash(package),
            'manifest': package.manifest.model_dump(mode='json'), 'source_matching': matching,
            'target': target, 'manual_selection': bool(options.source_id),
            'brief_revision': existing['revision'] if existing else None,
            'fields': preview_fields, 'evidence': resolved, 'evidence_counts': counts,
            'unresolved_count': counts['unresolved'] + counts['page_only'],
            'ambiguous_count': counts['candidates'], 'conflicts': conflicts, 'warnings': warnings,
            'duplicates': duplicates, 'can_commit': can_commit,
            'disposition': 'requires_review' if review_reasons else 'importable',
            'review_reasons': review_reasons,
            'knowledge_candidates': [r.model_dump(mode='json') for r in package.knowledge_items or []],
            'qa_candidates': [r.model_dump(mode='json') for r in package.qa_threads or []]}


def stage_preview(con, data: bytes, options: PreviewOptions) -> dict:
    package = validate_package(data)
    # Snapshot creation also needs a consistent DB view; no research content is written.
    con.execute('BEGIN IMMEDIATE')
    try:
        preview = build_preview(con, package, options)
        token = new_id()
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec='seconds')
        con.execute('INSERT INTO import_previews (id,payload_json,options_json,preview_json,created_at,expires_at) VALUES (?,?,?,?,?,?)',
                    (token, canonical(package.model_dump(mode='json')), canonical(options.model_dump()),
                     canonical(preview), now(), expires))
        con.commit()
        return {'preview_id': token, 'expires_at': expires, **preview}
    except Exception:
        con.rollback()
        raise


def commit_preview(con, token: str, *, confirmed: bool) -> dict:
    if not confirmed:
        raise PackageError('explicit confirmation required')
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT * FROM import_previews WHERE id=?', (token,)).fetchone()
        if not row:
            raise ImportConflict('preview not found')
        if row['committed_at']:
            raise ImportConflict('duplicate import: preview already committed')
        if row['expires_at'] <= now():
            raise ImportConflict('preview expired; create a fresh preview')
        package = Package.model_validate(json.loads(row['payload_json']))
        options = PreviewOptions.model_validate(json.loads(row['options_json']))
        fresh = build_preview(con, package, options)
        if fresh['duplicates']:
            raise ImportConflict('duplicate import: package ID or payload already imported')
        if canonical(fresh) != row['preview_json']:
            raise ImportConflict('stale preview: source/evidence/brief changed; create a fresh preview')
        if not fresh['can_commit']:
            raise ImportConflict('import requires source selection and writable fields')
        ts = now()
        writes = {}
        original_fields = brief_fields(package.paper_brief)
        for field in fresh['fields']:
            if field['action'] not in ('add', 'update'):
                continue
            original = original_fields[field['name']]
            evidence = list({e['evidence_id']: e for e in field['evidence_resolution'] +
                             [r for refs in field['result_evidence'].values() for r in refs]}.values())
            incoming = field['incoming']
            provenance = {**incoming['provenance'], 'package_id': package.manifest.package_id,
                'kgpack_schema_version': package.manifest.kgpack_schema_version,
                'schema_version': package.paper_brief.schema_version,
                'generator_label': package.manifest.generated_by, 'generated_at': package.manifest.generated_at,
                'generated_by': incoming['provenance'].get('generated_by') or package.manifest.generated_by,
                'imported_at': ts, 'imported_origin': incoming['origin'],
                'imported_user_edited': incoming['user_edited'], 'requested_status': original['status'],
                'original_field': original, 'evidence_status': {e['evidence_id']: e['status'] for e in evidence}}
            # External declarations of user origin never grant local user authority.
            writes[field['name']] = {**incoming, 'origin': 'chatgpt_import', 'user_edited': False,
                                    'provenance': provenance, 'evidence_resolution': evidence}
        written = write_fields(con, fresh['target']['id'], writes, automatic=True)
        con.execute('''INSERT INTO import_packages
            (package_id,payload_hash,source_id,manifest_json,payload_json,preview_json,imported_at) VALUES (?,?,?,?,?,?,?)''',
            (package.manifest.package_id, fresh['payload_hash'], fresh['target']['id'],
             canonical(package.manifest.model_dump(mode='json')), row['payload_json'], canonical(fresh), ts))
        con.execute('UPDATE import_previews SET committed_at=? WHERE id=?', (ts, token))
        con.commit()
        return {'package_id': package.manifest.package_id, 'source_id': fresh['target']['id'],
                'written_fields': written, 'preserved_fields': [f['name'] for f in fresh['fields'] if f['action'] == 'preserve_user'],
                'excluded_fields': options.exclude_fields, 'imported_at': ts}
    except Exception:
        con.rollback()
        raise
