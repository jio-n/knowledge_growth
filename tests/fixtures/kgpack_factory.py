"""Original synthetic package fixtures paired only with generated PDFs."""
from copy import deepcopy
import io
import json
import zipfile
from app.import_bridge.schema import Package
from app.import_bridge.validator import generate_package
from app.paper_brief import CORE_FIELDS


def field(value=None, *, status='not_reported', evidence=None, **kwargs):
    return {'value': value, 'status': status, 'evidence': evidence or [],
            'origin': 'llm', 'user_edited': False,
            'provenance': {'generated_by': 'Generated fixture', 'model': 'offline-fixture'}, **kwargs}


def package_data(source_hash=None):
    brief = {'schema_version': 'paper-brief-0.1', **{k: field() for k in CORE_FIELDS}}
    brief['paper_type'] = field('method', status='confirmed', evidence=['ev-unique'])
    brief['one_line_summary'] = field('An original synthetic method.', status='derived', evidence=['ev-unique'])
    brief['research_objective'] = field('Process synthetic input.', status='confirmed', evidence=['ev-unique'])
    brief['key_results'] = field([{
        'id': 'result-1', 'dataset': 'Synthetic-00', 'task': 'synthetic classification',
        'setting': 'few-shot', 'metric': 'Accuracy', 'score': 62.0, 'unit': '%',
        'split': 'test', 'comparison': 'Synthetic baseline: 60.0%',
        'status': 'confirmed', 'evidence': ['ev-result']}], status='confirmed', evidence=['ev-result'])
    return {
        'manifest': {'kgpack_schema_version': 'kgpack-0.1', 'package_id': 'synthetic-package',
                     'source_identity': {'title': 'Synthetic Research Fixture', 'authors': ['Fixture Author A'],
                                         'year': 2026, 'doi': None, 'arxiv_id': None, 'source_hash': source_hash},
                     'generated_by': 'Generated fixture', 'generated_at': '2026-10-04T00:00:00Z',
                     'paper_brief_schema_version': 'paper-brief-0.1',
                     'provenance_notice': 'Invented synthetic AI output; not a publication.',
                     'payloads': ['paper_brief.json', 'evidence_refs.json']},
        'paper_brief': brief,
        'evidence_refs': [
            {'evidence_id': 'ev-unique', 'page': 1, 'quote': 'A frozen backbone is evaluated in a few-shot setting.'},
            {'evidence_id': 'ev-result', 'page': 2, 'quote': 'Synthetic-00 | Accuracy | few-shot | 60.0 | 62.0', 'table_label': 'Table 1'}]}


def raw_zip(data, *, extra=None, compression=zipfile.ZIP_STORED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=compression) as archive:
        for name, value in data.items():
            if value is not None:
                archive.writestr(name + '.json', json.dumps(value, allow_nan=True))
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return out.getvalue()


def make_kgpack(name='valid', *, source_hash=None):
    data = deepcopy(package_data(source_hash))
    identity = data['manifest']['source_identity']
    if name == 'wrong_paper':
        identity.update(title='Unrelated generated source', authors=['Other Author'], year=2025, source_hash='f' * 64)
    elif name == 'doi_match':
        identity.update(source_hash=None, title='Alternate title', doi='https://doi.org/10.1234/SYNTHETIC')
    elif name == 'arxiv_match':
        identity.update(source_hash=None, title='Alternate title', arxiv_id='https://arxiv.org/abs/2601.00001v2')
    elif name == 'ambiguous_title':
        identity.update(source_hash=None)
    elif name == 'unique_quote':
        pass
    elif name == 'duplicate_quote':
        data['evidence_refs'][0]['quote'] = 'Shared evidence phrase appears on both pages.'
    elif name == 'missing_evidence':
        data['evidence_refs'][0].update(quote='No such invented evidence.', page=999)
    elif name == 'stale_bbox':
        data['evidence_refs'][0]['bbox'] = {'coordinate_system': 'pymupdf_unrotated', 'units': 'pt',
                                         'rect': [40, 700, 555, 710], 'page_rect': [0, 0, 595, 842], 'rotation': 0}
    elif name == 'key_result_without_evidence':
        result = data['paper_brief']['key_results']['value'][0]
        result.update(status='uncertain', evidence=[])
        data['paper_brief']['key_results']['status'] = 'uncertain'
    elif name in ('key_result_with_full_evidence', 'user_edited_conflict', 'duplicate_import'):
        pass
    elif name == 'unsafe_zip':
        return raw_zip(data, extra={'../escape.json': '{}'})
    elif name == 'malformed_zip':
        return b'not a ZIP file'
    elif name != 'valid':
        raise ValueError(name)
    return generate_package(Package.model_validate(data))


FIXTURE_NAMES = ('valid', 'wrong_paper', 'doi_match', 'arxiv_match', 'ambiguous_title', 'unique_quote',
                 'duplicate_quote', 'missing_evidence', 'stale_bbox', 'key_result_with_full_evidence',
                 'key_result_without_evidence', 'user_edited_conflict', 'duplicate_import', 'unsafe_zip', 'malformed_zip')
