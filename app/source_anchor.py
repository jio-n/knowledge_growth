"""Python SourceAnchor resolver following client/js/anchor.js (Phase 1).

The portable entry point strips foreign identity/index selectors before resolution.
Heading is a display hint; page never disambiguates repeated quotes.
"""
from __future__ import annotations

import json
import re
from pydantic import ValidationError
from .import_bridge.schema import Geometry, PortableEvidence


def norm(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def geometry(value):
    try:
        return Geometry.model_validate(json.loads(value) if isinstance(value, str) else value).model_dump()
    except (ValueError, TypeError, ValidationError):
        return None


def occurrences(block, anchor):
    text, quote = norm(block['text']), norm(anchor.get('quote'))
    if not quote:
        return []
    prefix, suffix = norm(anchor.get('prefix')), norm(anchor.get('suffix'))
    hits, start = [], 0
    while (idx := text.find(quote, start)) >= 0:
        if ((not prefix or text[:idx].rstrip().endswith(prefix)) and
            (not suffix or text[idx + len(quote):].lstrip().startswith(suffix))):
            hits.append(idx)
        start = idx + 1
    return hits


def overlap(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return width * height / min(area(a), area(b))


def _fail(candidates=(), reason='no_match'):
    unique = {b['id']: b for b in candidates}
    return {'status': 'candidates' if unique else 'unresolved', 'method': None,
            'block': None, 'page': None, 'candidates': list(unique.values()), 'reason': reason}


def _resolved(block, method):
    return {'status': 'resolved', 'method': method, 'block': block,
            'page': block.get('page'), 'candidates': [], 'reason': None}


def resolve_source_anchor(anchor, blocks, version):
    """Same resolution ordering and repeated-occurrence checks as Phase 1 JS."""
    if isinstance(anchor, str):
        try:
            anchor = json.loads(anchor)
        except ValueError:
            return _fail()
    if not isinstance(anchor, dict):
        return _fail()
    same_version = bool(anchor.get('sourceVersion')) and anchor['sourceVersion'] == version['id']
    same_source = bool(anchor.get('sourceHash')) and anchor['sourceHash'].lower() == (version.get('content_hash') or '').lower()
    quote = norm(anchor.get('quote'))
    has_quote = lambda b: not quote or quote in norm(b['text'])
    if same_version and anchor.get('blockId'):
        block = next((b for b in blocks if b['id'] == anchor['blockId'] and
                      b['version_id'] == version['id']), None)
        if block and has_quote(block):
            return _resolved(block, 'block_id')
    uncertain = []
    box = geometry(anchor.get('bbox'))
    if box and type(anchor.get('page')) is int and anchor['page'] > 0:
        hits = []
        for block in blocks:
            g = geometry(block.get('bbox') or block.get('bbox_json'))
            if (block['page'] == anchor['page'] and g and
                all(abs(a - b) < .01 for a, b in zip(g['page_rect'], box['page_rect'])) and
                overlap(g['rect'], box['rect']) >= .8):
                hits.append(block)
        supported = [b for b in hits if not quote or len(occurrences(b, anchor)) == 1]
        matches = [b for b in blocks for _ in occurrences(b, anchor)] if quote else []
        if len(supported) == 1 and (same_version or same_source or len(matches) == 1):
            return _resolved(supported[0], 'bbox')
        uncertain = supported or hits
    if quote:
        matches = [b for b in blocks for _ in occurrences(b, anchor)]
        if len(matches) == 1:
            return _resolved(matches[0], 'quote')
        if len(matches) > 1:
            return _fail(matches, 'ambiguous_quote')
        if any(has_quote(b) for b in blocks):
            return _fail([b for b in blocks if has_quote(b)], 'context_changed')
    if uncertain:
        return _fail(uncertain, 'ambiguous_bbox')
    if same_version and type(anchor.get('blockIdx')) is int:
        block = next((b for b in blocks if b['idx'] == anchor['blockIdx']), None)
        if block and has_quote(block):
            return _resolved(block, 'block_idx')
    if type(anchor.get('page')) is int and anchor['page'] > 0 and any(b['page'] == anchor['page'] for b in blocks):
        return {**_fail(), 'status': 'page_only', 'method': 'page', 'page': anchor['page'],
                'reason': 'evidence_unresolved'}
    return _fail()


def resolve_portable_evidence(evidence: PortableEvidence, blocks: list[dict], version: dict, *, source_hash=None) -> dict:
    anchor = {'type': 'text-quote', 'page': evidence.page, 'quote': evidence.quote,
              'prefix': evidence.prefix, 'suffix': evidence.suffix, 'headingPath': evidence.heading,
              'bbox': evidence.bbox.model_dump() if evidence.bbox else None,
              'sourceHash': evidence.source_hash or source_hash}
    result = resolve_source_anchor(anchor, blocks, version)
    # Label-only evidence targets captions, never a generic inline mention.
    # A failed quote/context must not be rescued by a label from a different text.
    labels = [s for s in (evidence.figure_label, evidence.table_label) if s]
    if not norm(evidence.quote) and labels and result['status'] != 'resolved':
        patterns = [re.compile(r'^' + re.escape(s).replace(r'\ ', r'\s+') + r'(?![A-Za-z0-9])', re.I)
                    for s in labels]
        hits = [b for b in blocks if any(p.search(norm(b['text'])) for p in patterns)]
        if len(hits) == 1:
            result = _resolved(hits[0], 'label')
        elif len(hits) > 1:
            result = _fail(hits, 'ambiguous_label')
    local_anchor = None
    if result['status'] == 'resolved':
        b = result['block']
        local_anchor = {**anchor, 'sourceVersion': version['id'], 'sourceHash': version.get('content_hash'),
                        'blockId': b['id'], 'blockIdx': b['idx'], 'page': b['page'],
                        'bbox': b.get('bbox'), 'headingPath': b.get('heading_path')}
        # Preserve the matched caption in label-only anchors across future versions.
        if not norm(local_anchor['quote']) and result['method'] == 'label':
            local_anchor['quote'] = b['text']
    return {'evidence_id': evidence.evidence_id, 'portable': evidence.model_dump(mode='json'),
            **result, 'anchor': local_anchor}
