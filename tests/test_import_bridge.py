"""Offline package, matching, portable Anchor and transaction acceptance tests."""
from copy import deepcopy
from contextlib import closing
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import zipfile

import pytest
from app.db import get_db
from app.import_bridge.matching import match_source
from app.import_bridge.schema import Package, SourceIdentity
from app.import_bridge.service import PreviewOptions, commit_preview, stage_preview
from app.import_bridge.validator import (MAX_MEMBER_BYTES, MAX_PACKAGE_BYTES, PackageError,
                                         generate_package, payload_hash, validate_package)
from app.paper_brief import FIELD_TYPES, PaperBrief, brief_fields
from app.source_anchor import resolve_portable_evidence, resolve_source_anchor
from tests.fixtures.kgpack_factory import field, make_kgpack, package_data, raw_zip
from tests.fixtures.pdf_factory import make_pdf


@pytest.fixture
def paper(client, monkeypatch):
    monkeypatch.setattr('app.routes_sources.run_analysis_async', lambda _: None)
    response = client.post('/api/sources/pdf', files={'file': ('generated.pdf', make_pdf('visual_evidence'), 'application/pdf')})
    assert response.status_code == 200
    source = response.json()['source']
    with closing(get_db()) as con:
        con.execute('UPDATE sources SET title=?,authors=?,year=2026,doi=?,arxiv_id=? WHERE id=?',
                    ('Synthetic Research Fixture', '["Fixture Author A"]', '10.1234/synthetic', '2601.00001', source['id']))
        con.commit()
    return source


def preview(client, data, **options):
    body = {key: json.dumps(value) if key == 'exclude_fields' else value for key, value in options.items()}
    return client.post('/api/import/kgpack/preview', files={'file': ('synthetic.kgpack', data, 'application/zip')}, data=body)


def commit(client, staged, confirmed=True):
    return client.post('/api/import/kgpack/commit', json={'preview_id': staged['preview_id'], 'confirmed': confirmed})


def staged_valid(client, paper):
    response = preview(client, make_kgpack(source_hash=paper['content_hash']))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('name', ['valid', 'wrong_paper', 'doi_match', 'arxiv_match', 'ambiguous_title',
    'unique_quote', 'duplicate_quote', 'missing_evidence', 'stale_bbox', 'key_result_with_full_evidence',
    'key_result_without_evidence', 'user_edited_conflict', 'duplicate_import'])
def test_generated_packages_validate_without_pdf_or_api_keys(name, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    package = validate_package(make_kgpack(name))
    assert package.manifest.kgpack_schema_version == 'kgpack-0.1'
    assert set(package.manifest.payloads) == {'paper_brief.json', 'evidence_refs.json'}


@pytest.mark.parametrize('mutate', [
    lambda d: d['manifest'].update(kgpack_schema_version='kgpack-99'),
    lambda d: d['manifest'].update(package_schema_version='kgpack-0.1'),
    lambda d: d['manifest'].pop('generated_by'),
    lambda d: d['manifest'].update(generated_at='2026-10-04'),
    lambda d: d['manifest']['source_identity'].update(unrecognized='value'),
    lambda d: d['manifest']['source_identity'].update(doi='not-a-doi'),
    lambda d: d['manifest']['source_identity'].update(arxiv_id='invalid'),
    lambda d: d['paper_brief'].update(schema_version='paper-brief-99'),
    lambda d: d['paper_brief'].pop('research_objective'),
    lambda d: d['paper_brief'].update(unsupported_field=field('invented')),
    lambda d: d['paper_brief']['paper_type'].update(value='invented'),
    lambda d: d['paper_brief']['paper_type'].update(status='verified'),
    lambda d: d['paper_brief']['paper_type'].update(user_edited='false'),
    lambda d: d['paper_brief']['paper_type'].update(evidence=['unknown']),
    lambda d: d['paper_brief']['paper_type'].update(evidence=['ev-unique', 'ev-unique']),
    lambda d: d['paper_brief']['paper_type'].update(provenance={'unknown': 'x'}),
    lambda d: d['evidence_refs'].append(deepcopy(d['evidence_refs'][0])),
    lambda d: d['evidence_refs'][0].update(status='resolved'),
    lambda d: d['evidence_refs'][0].update(page=0),
    lambda d: d['evidence_refs'][0].update(page='1'),
    lambda d: d['evidence_refs'][0].update(figure_label='Figure 2 or anything'),
    lambda d: d['paper_brief']['key_results']['value'][0].update(score=float('nan')),
    lambda d: d['paper_brief']['key_results']['value'][0].pop('dataset'),
    lambda d: d['paper_brief']['key_results']['value'][0].pop('setting'),
    lambda d: d['paper_brief']['key_results']['value'][0].pop('split'),
    lambda d: d['paper_brief']['key_results']['value'][0].update(evidence=[]),
    lambda d: d['paper_brief']['key_results']['value'][0].update(evidence=['unknown']),
    lambda d: d['paper_brief']['key_results']['value'].append(deepcopy(d['paper_brief']['key_results']['value'][0])),
])
def test_strict_schema_and_references(mutate):
    data = package_data()
    mutate(data)
    with pytest.raises(PackageError):
        validate_package(raw_zip(data))


@pytest.mark.parametrize('name', ['unsafe_zip', 'malformed_zip'])
def test_unsafe_generated_packages_refused(name):
    with pytest.raises(PackageError):
        validate_package(make_kgpack(name))


@pytest.mark.parametrize('path', ['../escape.json', '/absolute.json', 'nested/../../escape.json',
    'C:\\escape.json', 'paper.PDF', 'assets/figure.png', 'unknown.json', 'paper_brief.json/'])
def test_unexpected_members_and_pdf_refused(path, tmp_path):
    with pytest.raises(PackageError):
        validate_package(raw_zip(package_data(), extra={path: b'%PDF-' if path.endswith('PDF') else b'{}'}))
    assert list(tmp_path.iterdir()) == []


def test_duplicate_members_keys_links_and_oversize():
    data = raw_zip(package_data())
    out = io.BytesIO(data)
    with zipfile.ZipFile(out, 'a') as archive:
        with pytest.warns(UserWarning):
            archive.writestr('paper_brief.json', '{}')
    with pytest.raises(PackageError, match='duplicate ZIP'):
        validate_package(out.getvalue())
    for raw, message in [(b'{"same":1,"same":2}', 'duplicate JSON'),
                         (b' ' * (MAX_MEMBER_BYTES + 1), 'oversized'),
                         (b' ' * 100000, 'compression-ratio')]:
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in package_data().items():
                archive.writestr(name + '.json', raw if name == 'paper_brief' else json.dumps(value))
        with pytest.raises(PackageError, match=message):
            validate_package(out.getvalue())
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, value in package_data().items():
            entry = zipfile.ZipInfo(name + '.json')
            if name == 'paper_brief':
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, json.dumps(value))
    with pytest.raises(PackageError, match='non-regular'):
        validate_package(out.getvalue())
    with pytest.raises(PackageError, match='oversized package'):
        validate_package(b'x' * (MAX_PACKAGE_BYTES + 1))


@pytest.mark.parametrize('name,method', [('valid', 'content_hash'), ('doi_match', 'doi'), ('arxiv_match', 'arxiv_id')])
def test_strong_source_matching(client, paper, name, method):
    staged = preview(client, make_kgpack(name, source_hash=paper['content_hash'])).json()
    assert staged['source_matching']['status'] == 'matched'
    assert staged['source_matching']['strength'] == 'strong'
    assert staged['source_matching']['method'] == method
    assert staged['target']['id'] == paper['id']
    assert staged['can_commit']


def test_wrong_paper_and_weak_title_require_manual_selection(client, paper):
    wrong = preview(client, make_kgpack('wrong_paper')).json()
    assert wrong['source_matching']['status'] == 'unmatched'
    assert not wrong['can_commit']
    assert commit(client, wrong).status_code == 409
    weak = preview(client, make_kgpack('ambiguous_title')).json()
    assert weak['source_matching']['status'] == 'matched'
    assert weak['source_matching']['strength'] == 'weak'
    assert weak['target'] is None
    assert not weak['can_commit']
    selected = preview(client, make_kgpack('ambiguous_title'), source_id=paper['id']).json()
    assert selected['manual_selection'] and selected['can_commit']
    assert commit(client, selected).status_code == 200


def test_ambiguous_hash_doi_arxiv_title_and_identifier_conflicts(client, paper):
    second = client.post('/api/sources/pdf', files={'file': ('generated.pdf', make_pdf('visual_evidence'), 'application/pdf')}, data={'force': 'true'}).json()['source']
    with closing(get_db()) as con:
        con.execute('UPDATE sources SET title=?,authors=?,year=2026,doi=?,arxiv_id=? WHERE id=?',
                    ('Synthetic Research Fixture', '["Fixture Author A"]', '10.1234/synthetic', '2601.00001', second['id']))
        con.commit()
    for name in ('valid', 'doi_match', 'arxiv_match', 'ambiguous_title'):
        staged = preview(client, make_kgpack(name, source_hash=paper['content_hash'])).json()
        assert staged['source_matching']['status'] == 'ambiguous'
        assert staged['target'] is None and not staged['can_commit']
    client.delete('/api/sources/' + second['id'])
    data = package_data(paper['content_hash'])
    data['manifest']['source_identity']['doi'] = '10.9999/different'
    staged = preview(client, raw_zip(data)).json()
    assert staged['source_matching']['contradictions']
    assert staged['target'] is None and not staged['can_commit']


@pytest.mark.parametrize('name,status', [('unique_quote', 'resolved'), ('duplicate_quote', 'candidates'),
    ('missing_evidence', 'unresolved'), ('stale_bbox', 'resolved')])
def test_portable_quotes_and_status_downgrade(client, paper, name, status):
    staged = preview(client, make_kgpack(name, source_hash=paper['content_hash'])).json()
    resolved = staged['evidence'][0]
    assert resolved['status'] == status
    objective = next(f for f in staged['fields'] if f['name'] == 'research_objective')
    assert objective['incoming']['status'] == ('confirmed' if status == 'resolved' else 'uncertain')
    if status == 'resolved':
        assert resolved['anchor']['sourceVersion'] == staged['target']['version_id']
        assert resolved['anchor']['blockId'] == resolved['block']['id']
    else:
        assert resolved['anchor'] is None
    assert commit(client, staged).status_code == 200
    saved = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']['fields']['research_objective']
    assert saved['status'] == objective['incoming']['status']
    assert saved['verification'] == 'unverified'


def test_foreign_id_page_only_labels_context_and_bbox(client, paper):
    document = client.get('/api/sources/' + paper['id'] + '/document').json()
    blocks, version = document['blocks'], document['version']
    original = package_data(paper['content_hash'])
    from app.import_bridge.schema import PortableEvidence
    ev = PortableEvidence(evidence_id='foreign', block_id=blocks[0]['id'], page=2)
    assert resolve_portable_evidence(ev, blocks, version)['status'] == 'page_only'
    ev = PortableEvidence(evidence_id='caption', figure_label='Figure 1')
    assert resolve_portable_evidence(ev, blocks, version)['method'] == 'label'
    block = next(b for b in blocks if 'frozen backbone' in b['text'])
    ev = PortableEvidence(evidence_id='box', page=block['page'], bbox=block['bbox'])
    assert resolve_portable_evidence(ev, blocks, version)['status'] == 'candidates'
    ev.source_hash = version['content_hash']
    assert resolve_portable_evidence(ev, blocks, version)['status'] == 'resolved'
    ev.quote = original['evidence_refs'][0]['quote']
    ev.prefix = 'Wrong context.'
    assert resolve_portable_evidence(ev, blocks, version)['status'] == 'candidates'
    duplicate = [{'id': 'one', 'version_id': 'v', 'idx': 0, 'page': 1,
                  'text': 'Alpha. phrase. Beta. phrase.', 'bbox': None}]
    assert resolve_source_anchor({'quote': 'phrase.'}, duplicate, {'id': 'v'})['status'] == 'candidates'
    assert resolve_source_anchor({'quote': 'phrase.', 'prefix': 'Alpha.'}, duplicate, {'id': 'v'})['status'] == 'resolved'


def test_key_result_full_evidence_and_missing_evidence(client, paper):
    staged = staged_valid(client, paper)
    assert commit(client, staged).status_code == 200
    brief = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']
    result = brief['fields']['key_results']['value'][0]
    assert result == {'id': 'result-1', 'dataset': 'Synthetic-00', 'task': 'synthetic classification',
        'setting': 'few-shot', 'metric': 'Accuracy', 'score': 62.0, 'unit': '%', 'split': 'test',
        'comparison': 'Synthetic baseline: 60.0%', 'status': 'confirmed', 'evidence': ['ev-result']}
    data = package_data(paper['content_hash'])
    data['manifest']['package_id'] = 'missing-result'
    data['paper_brief']['key_results']['value'][0].update(status='uncertain', evidence=[])
    staged = preview(client, raw_zip(data)).json()
    assert commit(client, staged).status_code == 200
    saved = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']['fields']['key_results']
    assert saved['status'] == 'uncertain' and saved['value'][0]['status'] == 'uncertain'


def test_preview_is_not_research_write_and_confirmation_required(client, paper):
    staged = staged_valid(client, paper)
    with closing(get_db()) as con:
        assert con.execute('SELECT COUNT(*) FROM paper_briefs').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM import_packages').fetchone()[0] == 0
    assert commit(client, staged, confirmed=False).status_code == 422
    assert client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief'] is None
    assert commit(client, staged).status_code == 200
    brief = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']
    saved = brief['fields']['research_objective']
    assert saved['provenance']['package_id'] == 'synthetic-package'
    assert saved['provenance']['model'] == 'offline-fixture'
    assert saved['provenance']['imported_at']
    assert saved['origin'] == 'chatgpt_import' and not saved['user_edited']
    assert saved['verification'] == 'unverified'


def test_user_origin_and_user_edited_fields_preserved_and_exclusion(client, paper):
    assert commit(client, staged_valid(client, paper)).status_code == 200
    edit = client.patch('/api/sources/' + paper['id'] + '/paper-brief/fields/research_objective',
                        json=field('User corrected objective.', status='derived'))
    assert edit.status_code == 200
    with closing(get_db()) as con:
        con.execute("UPDATE paper_brief_fields SET user_edited=1,origin='llm' WHERE source_id=? AND field_name='background'", (paper['id'],))
        con.execute("UPDATE paper_brief_fields SET user_edited=0,origin='user' WHERE source_id=? AND field_name='problem'", (paper['id'],))
        con.commit()
    data = package_data(paper['content_hash'])
    data['manifest']['package_id'] = 'second-package'
    data['paper_brief']['research_objective']['value'] = 'Automatic replacement'
    staged = preview(client, raw_zip(data), exclude_fields=['one_line_summary']).json()
    conflicts = {c['field']: c for c in staged['conflicts']}
    assert conflicts['research_objective']['type'] == 'user_edited'
    assert conflicts['background']['policy'] == 'preserve_user'
    assert conflicts['problem']['policy'] == 'preserve_user'
    result = commit(client, staged).json()
    assert set(result['preserved_fields']) == {'research_objective', 'background', 'problem'}
    assert result['excluded_fields'] == ['one_line_summary']
    saved = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']['fields']
    assert saved['research_objective']['value'] == 'User corrected objective.'
    assert saved['research_objective']['origin'] == 'user'
    assert saved['background']['user_edited']
    assert saved['problem']['origin'] == 'user'
    assert saved['one_line_summary']['provenance']['package_id'] == 'synthetic-package'


def test_imported_claim_of_user_authority_is_only_provenance(client, paper):
    data = package_data(paper['content_hash'])
    data['paper_brief']['background'].update(origin='user', user_edited=True)
    assert commit(client, preview(client, raw_zip(data)).json()).status_code == 200
    saved = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']['fields']['background']
    assert saved['origin'] == 'chatgpt_import' and not saved['user_edited']
    assert saved['provenance']['imported_origin'] == 'user'
    assert saved['provenance']['imported_user_edited']


@pytest.mark.parametrize('change', ['field', 'version', 'block', 'source', 'expire'])
def test_stale_preview_requires_review(client, paper, change):
    staged = staged_valid(client, paper)
    with closing(get_db()) as con:
        if change == 'field':
            client.patch('/api/sources/' + paper['id'] + '/paper-brief/fields/research_objective', json=field('Edited.'))
        elif change == 'version':
            con.execute("INSERT INTO source_versions (id,source_id,fetched_at,content_hash) VALUES ('new-version',?,'9999',?)", (paper['id'], paper['content_hash']))
        elif change == 'block':
            bid = staged['evidence'][0]['block']['id']
            con.execute('UPDATE document_blocks SET text=? WHERE id=?', ('Changed original text.', bid))
        elif change == 'source':
            con.execute('UPDATE sources SET title=? WHERE id=?', ('Changed title', paper['id']))
        else:
            con.execute("UPDATE import_previews SET expires_at='2000' WHERE id=?", (staged['preview_id'],))
        con.commit()
    assert commit(client, staged).status_code == 409
    with closing(get_db()) as con:
        assert con.execute('SELECT COUNT(*) FROM import_packages').fetchone()[0] == 0


def test_duplicate_id_and_repacked_payload_rejected(client, paper):
    staged = staged_valid(client, paper)
    second_preview = staged_valid(client, paper)
    assert commit(client, staged).status_code == 200
    assert commit(client, staged).status_code == 409
    assert commit(client, second_preview).status_code == 409
    data = package_data(paper['content_hash'])
    data['manifest'].update(package_id='different-id', generated_at='2026-10-05T00:00:00Z')
    duplicate = preview(client, raw_zip(data)).json()
    assert duplicate['duplicates'] and not duplicate['can_commit']
    assert commit(client, duplicate).status_code == 409
    data['manifest']['package_id'] = 'synthetic-package'
    data['paper_brief']['background'] = field('Changed package content.')
    duplicate = preview(client, raw_zip(data)).json()
    assert duplicate['duplicates']


@pytest.mark.parametrize('failure', ['field', 'ledger', 'validation'])
def test_transaction_rollback_even_after_partial_writes(client, paper, failure):
    staged = staged_valid(client, paper)
    with closing(get_db()) as con:
        if failure == 'validation':
            con.execute("UPDATE import_previews SET payload_json='{}' WHERE id=?", (staged['preview_id'],))
        else:
            table = 'paper_brief_fields' if failure == 'field' else 'import_packages'
            condition = "WHEN NEW.field_name='research_objective'" if failure == 'field' else ''
            con.execute(f"CREATE TRIGGER import_failure BEFORE INSERT ON {table} {condition} BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        con.commit()
        if failure == 'validation':
            with pytest.raises(ValueError):
                commit_preview(con, staged['preview_id'], confirmed=True)
        else:
            with pytest.raises(sqlite3.IntegrityError):
                commit_preview(con, staged['preview_id'], confirmed=True)
        for table in ('paper_briefs', 'paper_brief_fields', 'import_packages'):
            assert con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        assert con.execute('SELECT committed_at FROM import_previews WHERE id=?', (staged['preview_id'],)).fetchone()[0] is None
        if failure != 'validation':
            con.execute('DROP TRIGGER import_failure')
            con.commit()
            assert commit_preview(con, staged['preview_id'], confirmed=True)['written_fields']


def test_optional_candidates_validated_previewed_archived_without_note_qa_write(client, paper):
    data = package_data(paper['content_hash'])
    data['manifest']['payloads'] += ['knowledge_items.json', 'qa_threads.json']
    data['knowledge_items'] = [{'id': 'note-1', 'section_key': 'method', 'content': 'Synthetic candidate.', 'evidence': ['ev-unique']}]
    data['qa_threads'] = [{'id': 'qa-1', 'title': 'Synthetic question', 'question': 'Why?', 'answer': 'Synthetic answer.', 'evidence': []}]
    staged = preview(client, raw_zip(data)).json()
    assert staged['knowledge_candidates'][0]['id'] == 'note-1'
    assert staged['qa_candidates'][0]['id'] == 'qa-1'
    assert 'optional_candidates_archived_only' in staged['review_reasons']
    assert commit(client, staged).status_code == 200
    with closing(get_db()) as con:
        assert con.execute('SELECT COUNT(*) FROM questions').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM knowledge_items').fetchone()[0] == 0
        archived = json.loads(con.execute('SELECT payload_json FROM import_packages').fetchone()[0])
        assert archived['knowledge_items'][0]['content'] == 'Synthetic candidate.'
    data['knowledge_items'][0]['verification'] = 'verified'
    with pytest.raises(PackageError):
        validate_package(raw_zip(data))


def test_all_extensions_roundtrip_through_dedicated_storage(client, paper):
    data = package_data(paper['content_hash'])
    strings = {'one_line_summary', 'research_objective', 'background', 'problem', 'proposed_method',
               'architecture_summary', 'model_size', 'inference_requirements', 'training_requirements'}
    for name in FIELD_TYPES:
        if name in data['paper_brief']:
            continue
        value = 1 if name == 'shots' else {'code': 'https://example.invalid/code', 'compute': 'not reported'} if name == 'reproducibility' else 'Synthetic summary' if name in strings else ['Synthetic component']
        data['paper_brief'][name] = field(value, status='derived', evidence=['ev-unique'])
    staged = preview(client, raw_zip(data)).json()
    assert commit(client, staged).status_code == 200
    saved = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']
    assert set(saved['fields']) == set(FIELD_TYPES)
    expected = brief_fields(PaperBrief.model_validate(data['paper_brief']))
    for name, f in saved['fields'].items():
        assert f['value'] == expected[name]['value']
        assert f['provenance']['model'] == 'offline-fixture'


def test_cli_generate_validate_failure_and_no_database_side_effects(tmp_path):
    env = {**os.environ, 'KG_DATA_DIR': str(tmp_path / 'no-db'), 'KG_LLM_PROVIDER': 'mock'}
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        env.pop(key, None)
    input_path, output = tmp_path / 'template.json', tmp_path / 'sample.kgpack'
    input_path.write_text(json.dumps(package_data()), encoding='utf-8')
    base = [sys.executable, '-m', 'app.import_bridge']
    generated = subprocess.run(base + ['generate', str(input_path), str(output)], env=env, capture_output=True, text=True)
    assert generated.returncode == 0, generated.stderr
    result = subprocess.run(base + ['validate', str(output)], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['valid']
    output.write_bytes(make_kgpack('unsafe_zip'))
    result = subprocess.run(base + ['validate', str(output)], env=env, capture_output=True, text=True)
    assert result.returncode == 1 and not json.loads(result.stderr)['valid']
    assert not (tmp_path / 'no-db').exists()


def test_api_validation_failures_and_exclusions(client, paper):
    response = client.post('/api/import/kgpack/validate', files={'file': ('unsafe.kgpack', make_kgpack('unsafe_zip'))})
    assert response.status_code == 422
    response = client.post('/api/import/kgpack/validate', files={'file': ('valid.kgpack', make_kgpack())})
    assert response.status_code == 200 and response.json()['valid']
    for opts in ({'source_id': 'does-not-exist'}, {'exclude_fields': ['unknown']},
                 {'exclude_fields': ['background', 'background']}):
        assert preview(client, make_kgpack(source_hash=paper['content_hash']), **opts).status_code == 422
    assert client.get('/api/sources/no-source/paper-brief').status_code == 404
    assert client.patch('/api/sources/' + paper['id'] + '/paper-brief/fields/unknown', json=field()).status_code == 422
    with closing(get_db()) as con:
        assert con.execute('SELECT COUNT(*) FROM paper_briefs').fetchone()[0] == 0


def test_result_evidence_preserved_when_not_repeated_at_parent_field(client, paper):
    data = package_data(paper['content_hash'])
    data['paper_brief']['key_results'].update(status='derived', evidence=[])
    assert commit(client, preview(client, raw_zip(data)).json()).status_code == 200
    saved = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()['paper_brief']['fields']['key_results']
    assert saved['evidence'] == []
    assert saved['evidence_resolution'][0]['anchor']['blockId']
    assert saved['value'][0]['status'] == 'confirmed'
    changed = field(saved['value'], status='derived')
    assert client.patch('/api/sources/' + paper['id'] + '/paper-brief/fields/key_results', json=changed).status_code == 200


@pytest.mark.parametrize('case', ['id', 'bbox', 'stale_bbox', 'duplicate', 'context', 'page', 'index', 'unresolved'])
def test_python_phase1_anchor_parity(case):
    from tests.test_pdf_geometry import document, resolve
    blocks, version = document('duplicate_evidence')
    block = next(b for b in blocks if b['text'].startswith('Alpha'))
    anchor = {'quote': 'Shared evidence phrase.', 'page': 1}
    if case == 'id':
        anchor.update(sourceVersion='v1', blockId=block['id'])
    elif case == 'bbox':
        anchor.update(sourceVersion='v1', bbox=block['bbox'])
    elif case == 'stale_bbox':
        anchor.update(sourceVersion='old', bbox=block['bbox'])
    elif case == 'context':
        anchor.update(prefix='Alpha before.', suffix='Alpha after.')
    elif case == 'page':
        anchor['quote'] = 'missing'
    elif case == 'index':
        anchor = {'sourceVersion': 'v1', 'blockIdx': block['idx']}
    elif case == 'unresolved':
        anchor = {'page': 999, 'quote': 'missing'}
    python = resolve_source_anchor(anchor, blocks, version)
    browser = resolve(anchor, blocks, version)
    assert python == browser


@pytest.mark.parametrize('kind', ['prefix', 'suffix', 'comment', 'orphan'])
def test_pdf_cannot_be_hidden_outside_listed_payloads(kind):
    data = raw_zip(package_data())
    if kind == 'prefix':
        data = make_pdf() + data
    elif kind == 'suffix':
        data += make_pdf()
    elif kind == 'comment':
        out = io.BytesIO(data)
        with zipfile.ZipFile(out, 'a') as archive:
            archive.comment = b'not supported in kgpack-0.1'
        data = out.getvalue()
    else:
        # Rewriting an archive entry leaves an orphan local file record.
        out = io.BytesIO(data)
        with zipfile.ZipFile(out, 'a') as archive:
            entry = next(m for m in archive.filelist if m.filename == 'paper_brief.json')
            archive.filelist.remove(entry)
            del archive.NameToInfo['paper_brief.json']
            archive.writestr('paper_brief.json', json.dumps(package_data()['paper_brief']))
        data = out.getvalue()
    with pytest.raises(PackageError):
        validate_package(data)


def test_streaming_zip_with_data_descriptors_is_compatible():
    class Streaming(io.BytesIO):
        def seekable(self):
            return False

        def seek(self, *args):
            raise OSError('unseekable')
    out = Streaming()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in package_data().items():
            archive.writestr(name + '.json', json.dumps(value))
    assert validate_package(out.getvalue()).manifest.package_id == 'synthetic-package'


def test_user_edit_rechecks_evidence_after_local_version_changes(client, paper):
    assert commit(client, staged_valid(client, paper)).status_code == 200
    with closing(get_db()) as con:
        con.execute("INSERT INTO source_versions (id,source_id,fetched_at,content_hash) VALUES ('different',?,'9999',?)", (paper['id'], '0' * 64))
        con.commit()
    changed = field('Explicit edited statement.', status='confirmed', evidence=['ev-unique'])
    response = client.patch('/api/sources/' + paper['id'] + '/paper-brief/fields/research_objective', json=changed)
    assert response.status_code == 200
    saved = response.json()['paper_brief']['fields']['research_objective']
    assert saved['status'] == 'uncertain'
    assert saved['origin'] == 'user' and saved['user_edited']
    assert saved['evidence_resolution'][0]['status'] == 'unresolved'


def test_package_hash_can_support_bbox_but_foreign_hash_cannot(client, paper):
    document = client.get('/api/sources/' + paper['id'] + '/document').json()
    block = next(b for b in document['blocks'] if 'frozen backbone' in b['text'])
    data = package_data(paper['content_hash'])
    data['evidence_refs'][0].update(quote=None, bbox=block['bbox'])
    staged = preview(client, raw_zip(data)).json()
    assert staged['evidence'][0]['method'] == 'bbox'
    assert staged['evidence'][0]['status'] == 'resolved'
    data['manifest']['source_identity'].update(source_hash='f' * 64, doi='10.1234/synthetic')
    staged = preview(client, raw_zip(data)).json()
    assert staged['evidence'][0]['status'] == 'candidates'


@pytest.mark.parametrize('kind', ['unsupported_compression', 'encrypted', 'corrupt_crc', 'null_name'])
def test_hostile_zip_metadata_and_corruption_are_validation_errors(kind):
    import struct
    if kind == 'unsupported_compression':
        data = raw_zip(package_data(), compression=zipfile.ZIP_BZIP2)
    else:
        data = bytearray(raw_zip(package_data()))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entry = archive.infolist()[0]
            central = archive.start_dir
            local = entry.header_offset
        if kind == 'encrypted':
            struct.pack_into('<H', data, local + 6, 1)
            struct.pack_into('<H', data, central + 8, 1)
        elif kind == 'corrupt_crc':
            header = struct.unpack_from('<4s5H3I2H', data, local)
            body = local + 30 + header[-2] + header[-1]
            data[body] ^= 1
        else:
            # Python's ZIP parser truncates at NUL; the original name must still be refused.
            name = b'manifest.json'
            data[local + 30 + len(name) - 1] = 0
            data[central + 46 + len(name) - 1] = 0
        data = bytes(data)
    with pytest.raises(PackageError):
        validate_package(data)


def test_import_update_failure_restores_existing_brief_and_research_data(client, paper):
    assert commit(client, staged_valid(client, paper)).status_code == 200
    client.post('/api/knowledge', json={'source_id': paper['id'], 'section_key': 'method',
        'content': 'Preserve user note.', 'origin': 'user', 'info_type': 'user_thought',
        'verification': 'verified', 'anchor': {'quote': 'Preserved anchor'}})
    before = client.get('/api/sources/' + paper['id'] + '/paper-brief').json()
    data = package_data(paper['content_hash'])
    data['manifest']['package_id'] = 'update-rollback'
    data['paper_brief']['background'] = field('Changed background.', status='derived')
    staged = preview(client, raw_zip(data)).json()
    with closing(get_db()) as con:
        note = dict(con.execute('SELECT * FROM knowledge_items').fetchone())
        con.execute("CREATE TRIGGER fail_update BEFORE INSERT ON import_packages BEGIN SELECT RAISE(ABORT,'injected'); END")
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            commit_preview(con, staged['preview_id'], confirmed=True)
        assert con.execute('SELECT COUNT(*) FROM import_packages').fetchone()[0] == 1
        assert dict(con.execute('SELECT * FROM knowledge_items').fetchone()) == note
    assert client.get('/api/sources/' + paper['id'] + '/paper-brief').json() == before


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_valid_json_prefix_cannot_hide_more_pdf_data_in_member(compression):
    import struct
    import zlib
    data = package_data()
    out = io.BytesIO()
    manifest = json.dumps(data['manifest']).encode()
    with zipfile.ZipFile(out, 'w', compression=compression) as archive:
        archive.writestr('manifest.json', manifest + make_pdf())
        archive.writestr('paper_brief.json', json.dumps(data['paper_brief']))
        archive.writestr('evidence_refs.json', json.dumps(data['evidence_refs']))
    raw = bytearray(out.getvalue())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        first, central = archive.infolist()[0], archive.start_dir
    # Standard ZIP readers trust file_size and return the valid JSON prefix only.
    struct.pack_into('<I', raw, first.header_offset + 14, zlib.crc32(manifest))
    struct.pack_into('<I', raw, first.header_offset + 22, len(manifest))
    struct.pack_into('<I', raw, central + 16, zlib.crc32(manifest))
    struct.pack_into('<I', raw, central + 24, len(manifest))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.read('manifest.json') == manifest
    with pytest.raises(PackageError):
        validate_package(bytes(raw))
