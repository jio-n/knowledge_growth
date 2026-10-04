"""Local PDF structure and bounded, deterministic selection. No AI calls here."""
import json
import re
from .import_bridge.validator import canonical

CLASSIFY_BUDGET = 14000
EXTRACT_BUDGET = 48000
MAX_BLOCK_TEXT = 2000
MAX_SECTIONS = 160
CAPTION = re.compile(r'^(Figure|Fig\.|Table)\s*(\d+[A-Za-z]?)\b', re.I)
FOCUS = re.compile(r'abstract|introduction|method|approach|model|architecture|experiment|evaluat|result|benchmark|dataset|ablation|limit|discussion|conclu|reproduc|implementation', re.I)


def load_document(con, source_id):
    source = con.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
    if not source or source['type'] != 'pdf':
        raise ValueError('registered PDF required')
    version = con.execute('SELECT * FROM source_versions WHERE source_id=? ORDER BY fetched_at DESC,rowid DESC LIMIT 1', (source_id,)).fetchone()
    if not version:
        raise ValueError('PDF version required')
    blocks = [dict(b) for b in con.execute('SELECT * FROM document_blocks WHERE version_id=? ORDER BY idx', (version['id'],))]
    if not blocks:
        raise ValueError('PDF text blocks required')
    for b in blocks:
        b['bbox'] = json.loads(b['bbox_json']) if b['bbox_json'] else None
    return dict(source), dict(version), blocks


def structure(source, version, blocks):
    sections, mapping, grouped = [], {}, {}
    for i, b in enumerate(blocks):
        path = b.get('heading_path') or '(front matter)'
        if path not in grouped:
            s = {'id': f'S{len(sections) + 1:04d}', 'heading_path': path,
                 'level': b.get('level'), 'parent_path': path.rpartition(' > ')[0], 'blocks': []}
            sections.append(s)
            grouped[path] = s
        eid = f'B{i + 1:06d}'
        mapping[eid] = b
        match = CAPTION.match(b['text'].strip())
        item = {'reference_id': eid, 'block_id': b['id'], 'page': b['page'],
                'heading_path': path, 'kind': b['kind'], 'role': b.get('role'),
                'bbox': {k: v for k, v in b['bbox'].items() if k != 'spans'} if b['bbox'] else None,
                'text': b['text'], 'candidate_label': match.group(0) if match else None}
        grouped[path]['blocks'].append(item)
    authors = source.get('authors')
    if isinstance(authors, str):
        try: authors = json.loads(authors)
        except ValueError: authors = []
    return {'metadata': {'title': source['title'], 'authors': authors or [], 'year': source.get('year'),
                         'source_version': version['id']}, 'sections': sections}, mapping


def select_context(doc, *, stage, selected_sections=()):
    """Round-robin section coverage with a hard serialized-character budget.

    Classification starts with Abstract/front matter + Introduction + Conclusion.
    Extraction prioritizes requested sections and local method/result/caption hints.
    A block prefix is explicitly marked when truncated; Evidence retains full text.
    """
    budget = CLASSIFY_BUDGET if stage == 'classify' else EXTRACT_BUDGET
    selection_budget = budget if stage == 'classify' else budget - 2000  # bounded classification hints
    sections = doc['sections']
    def priority(s):
        path = s['heading_path']
        if stage == 'classify':
            return 0 if re.search(r'abstract|front matter', path, re.I) else 1 if re.search(r'introduction|conclu', path, re.I) else 2
        return 0 if s['id'] in selected_sections else 1 if FOCUS.search(path) else 2
    ordered = sorted(sections, key=priority)
    hierarchy = [{k: s[k] for k in ('id', 'heading_path', 'level', 'parent_path')} for s in ordered[:MAX_SECTIONS]]
    # Bound exceptionally long headings/metadata independently of paper length.
    for s in hierarchy:
        s['heading_path'] = s['heading_path'][:300]
        s['parent_path'] = s['parent_path'][:300]
    metadata = {k: v for k, v in doc['metadata'].items()}
    metadata['title'] = str(metadata['title'])[:1000]
    metadata['authors'] = [str(a)[:200] for a in metadata['authors'][:32]]
    # Escaped control characters can cost six serialized characters each.
    # Bound the serialized metadata, not just its nominal string lengths.
    metadata_truncated = False
    while len(canonical(metadata)) > 3000:
        metadata_truncated = True
        if metadata['authors']:
            metadata['authors'].pop()
        else:
            metadata['title'] = metadata['title'][:len(metadata['title']) // 2]
    context = {'stage': stage, 'metadata': metadata, 'section_hierarchy': hierarchy,
               'abstract_candidates': [], 'blocks': [], 'coverage': {}}
    # Preserve useful capacity if an extreme number of headings fills the budget.
    while len(canonical(context)) > budget // 3 and hierarchy:
        hierarchy.pop()
    queues = []
    section_ids = {s['heading_path']: s['id'] for s in sections}
    size = len(canonical(context)) + 600
    for s in ordered:
        candidates = sorted(s['blocks'], key=lambda b: 0 if b['candidate_label'] else 1) if stage != 'classify' else s['blocks']
        queues.append(iter(candidates))
    used = set()
    active = queues
    while active:
        following = []
        for queue in active:
            b = next(queue, None)
            if b is None: continue
            following.append(queue)
            item = {**b, 'text': b['text'][:MAX_BLOCK_TEXT], 'text_truncated': len(b['text']) > MAX_BLOCK_TEXT,
                    'heading_path': b['heading_path'][:300]}
            item['section_id'] = section_ids[b['heading_path']]
            cost = len(canonical(item)) + 1
            if size + cost > selection_budget:
                continue
            context['blocks'].append(item)
            size += cost
            used.add(item['reference_id'])
        active = following
    context['abstract_candidates'] = [b['reference_id'] for b in context['blocks'] if re.search(r'abstract', b['heading_path'], re.I)][:8]
    if not context['abstract_candidates']:
        context['abstract_candidates'] = [b['reference_id'] for b in context['blocks'][:3]]
    context['coverage'] = {'total_blocks': sum(len(s['blocks']) for s in sections), 'sent_blocks': len(used),
                           'omitted_blocks': sum(len(s['blocks']) for s in sections) - len(used),
                           'truncated_blocks': sum(b['text_truncated'] for b in context['blocks']),
                           'omitted_sections': len(sections) - len(hierarchy), 'character_budget': budget,
                           'metadata_truncated': metadata_truncated}
    return context, used
