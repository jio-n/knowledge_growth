"""T2-02/03/06 observable acceptance through fake AIRuntime and generated PDFs."""
from contextlib import closing
from copy import deepcopy
import json
import pytest
from app.ai.offline import NoAIRuntime
from app.brief_context import structure, select_context, CLASSIFY_BUDGET, EXTRACT_BUDGET
from app.brief_generation import generate, GenerationError
from app.db import get_db, now
from app.import_bridge.validator import canonical
from tests.fixtures.brief_runtime import FakeBriefRuntime, paper_pdf, field


@pytest.fixture
def paper(client, monkeypatch):
    monkeypatch.setattr('app.routes_sources.run_analysis_async', lambda _: None)
    res = client.post('/api/sources/pdf', files={'file': ('generated.pdf', paper_pdf(), 'application/pdf')})
    assert res.status_code == 200
    return res.json()['source']['id']


def start(client, sid, runtime=None):
    if runtime is not None: client.app.state.ai_runtime = runtime
    res = client.post(f'/api/sources/{sid}/paper-brief/generate')
    assert res.status_code == 202, res.text
    return client.get(f'/api/sources/{sid}/paper-brief/generation').json()


def commit(client, sid, job):
    return client.post(f'/api/sources/{sid}/paper-brief/generation/commit', json={'generation_id': job['generation_id'], 'confirmed': True})


def brief(client, sid):
    return client.get(f'/api/sources/{sid}/paper-brief').json()['paper_brief']


@pytest.mark.parametrize('kind', ['method', 'benchmark', 'survey', 'dataset', 'analysis', 'system', 'position', 'other'])
def test_types_and_pipeline(client, paper, kind, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    # Replace generated text with the corresponding generated paper type.
    with closing(get_db()) as con:
        con.execute('UPDATE document_blocks SET text=replace(text,?,?) WHERE source_id=?', ('This method', f'This {kind}', paper))
        con.commit()
    runtime = FakeBriefRuntime(kind)
    job = start(client, paper, runtime)
    assert job['state'] == 'preview', job
    assert job['preview']['mode'] == 'initial'
    assert brief(client, paper) is None  # Preview cannot write research content.
    assert len(runtime.calls) == 2
    assert all(c['instructions'].startswith('# paper-brief-extract-0.1') for c in runtime.calls)
    assert commit(client, paper, job).status_code == 200
    saved = brief(client, paper)['fields']
    assert saved['paper_type']['value'] == kind
    assert saved['paper_type']['status'] == 'confirmed'
    assert saved['model_size']['status'] == 'not_reported'
    assert saved['shots']['value'] is None
    assert saved['limitations']['status'] == 'not_reported'  # Missing optional field.
    assert saved['failure_cases']['status'] == 'uncertain'
    if kind in ('benchmark', 'survey'):
        assert saved['proposed_method']['status'] == 'not_applicable'
    result = saved['key_results']['value'][0]
    assert {'dataset', 'task', 'setting', 'metric', 'score', 'unit', 'split', 'comparison', 'status', 'evidence'} <= result.keys()
    assert result['status'] == 'confirmed'
    for name in ('key_results', 'important_figures', 'important_tables'):
        ev = saved[name]['evidence_resolution'][0]
        assert ev['status'] == 'resolved'
        anchor = ev['anchor']
        document = client.get(f'/api/sources/{paper}/document').json()
        assert any(b['id'] == anchor['blockId'] and b['page'] == anchor['page'] and b['bbox'] == anchor['bbox'] for b in document['blocks'])
    for f in saved.values():
        assert {'generated_by', 'runtime', 'model', 'prompt_version', 'generated_at', 'source_version', 'schema_version'} <= f['provenance'].keys()
        assert f['origin'] == 'llm' and f['verification'] == 'unverified'
        assert not any('thread' in k for k in f['provenance'])


@pytest.mark.parametrize('mutate', [
    lambda d, c: d['paper_brief'].update(unknown=field()),
    lambda d, c: d['paper_brief']['paper_type'].update(status='verified'),
    lambda d, c: d['paper_brief'].pop('target_task'),
    lambda d, c: d['paper_brief']['paper_type'].update(value=4),
    lambda d, c: d['paper_brief']['paper_type'].update(evidence=['B999999']),
    lambda d, c: d['paper_brief']['paper_type']['evidence'].append(d['paper_brief']['paper_type']['evidence'][0]),
    lambda d, c: d['evidence_refs'].extend([{'evidence_id':'ev1', 'quote':'This'}, {'evidence_id':'ev1', 'quote':'This'}]),
    lambda d, c: d['evidence_refs'].append({'evidence_id':'ev1', 'page':1, 'block_id':'B999999'}),
    lambda d, c: d['paper_brief'].update(important_figures=field(['Figure 1'], 'confirmed')),
    lambda d, c: d['paper_brief']['key_results']['value'][0].pop('setting') if c['stage'] == 'extract' else None,
    lambda d, c: d['paper_brief']['key_results']['value'][0].update(evidence=[]) if c['stage'] == 'extract' else None,
    lambda d, c: d['paper_brief']['key_results']['value'].append(deepcopy(d['paper_brief']['key_results']['value'][0])) if c['stage'] == 'extract' else None,
])
def test_invalid_outputs_bounded_and_existing_brief_kept(client, paper, mutate):
    original = start(client, paper, FakeBriefRuntime())
    assert commit(client, paper, original).status_code == 200
    before = brief(client, paper)
    runtime = FakeBriefRuntime(mutate=mutate)
    job = start(client, paper, runtime)
    assert job['state'] == 'failed' and job['error'] == 'invalid_output'
    assert len(runtime.calls) <= 3
    assert brief(client, paper) == before


def test_json_retry_success_and_exhaustion(client, paper):
    runtime = FakeBriefRuntime(invalid_attempts=1)
    job = start(client, paper, runtime)
    assert job['state'] == 'preview' and len(runtime.calls) == 3
    assert commit(client, paper, job).status_code == 200
    before = brief(client, paper)
    runtime = FakeBriefRuntime(invalid_attempts=9)
    failed = start(client, paper, runtime)
    assert failed['state'] == 'failed' and len(runtime.calls) == 2
    assert 'PRIVATE_OUTPUT_SENTINEL' not in canonical(failed)
    assert brief(client, paper) == before


@pytest.mark.parametrize('selector,status', [
    ({'quote':'does not exist'}, 'unresolved'),
    ({'quote':'Synthetic'}, 'candidates'),
    ({'page':3, 'quote':'does not exist'}, 'page_only'),
    ({'quote':'Baseline Accuracy 60%.'}, 'resolved'),
])
def test_portable_fallback_downgrade(client, paper, selector, status):
    def mutate(d, c):
        if c['stage'] != 'extract': return
        d['evidence_refs'] = [{'evidence_id':'ev1', **selector}]
        r = d['paper_brief']['key_results']
        r['evidence'] = ['ev1']
        r['value'][0]['evidence'] = ['ev1']
    job = start(client, paper, FakeBriefRuntime(mutate=mutate))
    assert job['state'] == 'preview', job
    assert commit(client, paper, job).status_code == 200
    f = brief(client, paper)['fields']['key_results']
    assert f['evidence_resolution'][0]['status'] == status
    assert f['value'][0]['status'] == ('confirmed' if status == 'resolved' else 'uncertain')


def test_derived_number_without_evidence_becomes_uncertain(client, paper):
    def mutate(d,c):
        if c['stage'] == 'extract': d['paper_brief']['key_results']['value'][0].update(status='derived', evidence=[])
    job = start(client, paper, FakeBriefRuntime(mutate=mutate))
    assert commit(client, paper, job).status_code == 200
    assert brief(client, paper)['fields']['key_results']['value'][0]['status'] == 'uncertain'


def test_regeneration_preserves_edits_and_previews_actions(client, paper):
    job = start(client, paper, FakeBriefRuntime())
    assert commit(client, paper, job).status_code == 200
    base = f'/api/sources/{paper}/paper-brief'
    assert client.patch(base + '/fields/research_objective', json=field('User objective', 'derived')).status_code == 200
    with closing(get_db()) as con:
        con.execute("DELETE FROM paper_brief_fields WHERE source_id=? AND field_name='limitations'", (paper,))
        con.execute("UPDATE paper_brief_fields SET value_json='\"Old generated summary\"' WHERE source_id=? AND field_name='one_line_summary'", (paper,))
        # Test origin=user without user_edited=true independently of PATCH policy.
        con.execute("UPDATE paper_brief_fields SET origin='user',user_edited=0 WHERE source_id=? AND field_name='model_size'", (paper,))
        con.commit()
    before = brief(client, paper)
    job = start(client, paper)
    assert job['preview']['mode'] == 'regenerate_preserve_user'
    actions = {f['name']: f['action'] for f in job['preview']['fields']}
    assert actions['research_objective'] == actions['model_size'] == 'preserve_user'
    assert actions['one_line_summary'] == 'generated_update'
    assert actions['limitations'] == 'new_field'
    assert actions['shots'] == 'unchanged'
    assert brief(client, paper) == before
    assert commit(client, paper, job).status_code == 200
    after = brief(client, paper)
    assert after['fields']['research_objective'] == before['fields']['research_objective']
    assert after['fields']['model_size'] == before['fields']['model_size']
    assert after['fields']['one_line_summary']['value'] == 'Synthetic classification study'
    assert commit(client, paper, job).status_code == 409


@pytest.mark.parametrize('change', ['edit', 'block', 'source'])
def test_stale_preview_atomic_no_overwrite(client, paper, change):
    job = start(client, paper, FakeBriefRuntime())
    if change == 'edit':
        client.patch(f'/api/sources/{paper}/paper-brief/fields/research_objective', json=field('New user edit', 'derived'))
    else:
        with closing(get_db()) as con:
            if change == 'block': con.execute("UPDATE document_blocks SET text=text||' changed' WHERE source_id=?", (paper,))
            else: con.execute("UPDATE sources SET title='Changed' WHERE id=?", (paper,))
            con.commit()
    before = brief(client, paper)
    assert commit(client, paper, job).status_code == 409
    assert brief(client, paper) == before


def test_no_ai_and_api_key_free_pdf_brief_fallback(client, paper, monkeypatch):
    job = start(client, paper, FakeBriefRuntime())
    assert commit(client, paper, job).status_code == 200
    before = brief(client, paper)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    client.app.state.ai_runtime = NoAIRuntime()
    assert client.post(f'/api/sources/{paper}/paper-brief/generate').status_code == 503
    assert brief(client, paper) == before
    assert client.get(f'/api/sources/{paper}/file').status_code == 200
    assert client.get(f'/api/sources/{paper}/document').status_code == 200


def test_mock_runtime_reuses_existing_pipeline(client, paper):
    job = start(client, paper)
    assert job['state'] == 'preview', job
    assert commit(client, paper, job).status_code == 200
    assert brief(client, paper)['fields']['one_line_summary']['value'].startswith('【モック抽出】')


def test_long_context_bounded_with_hierarchy_and_local_ids():
    blocks = [{'id':f'b{i}', 'idx':i, 'kind':'para', 'text':'Synthetic text ' * 500, 'page':i//10+1,
               'bbox':None, 'heading_path':f'{i//10} Results', 'level':None, 'role':'para'} for i in range(3000)]
    doc, mapping = structure({'title':'Generated long paper', 'authors':'["Author"]', 'year':2026}, {'id':'v1'}, blocks)
    for stage, limit in [('classify', CLASSIFY_BUDGET), ('extract', EXTRACT_BUDGET)]:
        context, ids = select_context(doc, stage=stage)
        assert len(canonical(context)) <= limit
        assert context['coverage']['omitted_blocks'] > 0
        assert context['coverage']['truncated_blocks'] > 0
        assert ids <= mapping.keys()
        assert context['section_hierarchy']
        assert all({'block_id','bbox','page','heading_path','section_id','reference_id','text'} <= b.keys() for b in context['blocks'])


def test_origin_cannot_be_forged_by_model(client, paper):
    def mutate(d,c):
        d['paper_brief']['one_line_summary'].update(origin='user',user_edited=True,provenance={'model':'forged'})
    job = start(client, paper, FakeBriefRuntime(mutate=mutate))
    assert commit(client, paper, job).status_code == 200
    f = brief(client, paper)['fields']['one_line_summary']
    assert f['origin'] == 'llm' and not f['user_edited']
    assert f['provenance']['model'] == 'mock'


def test_title_only_classification_is_not_confirmed(client, paper):
    def mutate(d,c):
        heading = next(b for b in c['blocks'] if b['kind']=='heading')
        d['paper_brief']['paper_type'].update(evidence=[heading['reference_id']])
    job = start(client, paper, FakeBriefRuntime(mutate=mutate))
    assert commit(client, paper, job).status_code == 200
    assert brief(client, paper)['fields']['paper_type']['status'] == 'uncertain'


def test_runtime_failure_safe_and_retryable(client, paper):
    from app.ai.base import AIError
    class Failing(FakeBriefRuntime):
        def send_turn(self,*args,**kwargs): raise AIError('turn_failed')
    job = start(client, paper, Failing())
    assert job['state']=='failed' and job['error']=='runtime_failed'
    assert brief(client, paper) is None
    assert start(client, paper, FakeBriefRuntime())['state']=='preview'


def test_block_quote_mismatch_refused(client, paper):
    def mutate(d,c):
        ref=d['paper_brief']['paper_type']['evidence'][0]
        d['paper_brief']['paper_type']['evidence']=['ev1']
        d['evidence_refs']=[{'evidence_id':'ev1','block_id':ref,'quote':'Fabricated quote'}]
    job=start(client,paper,FakeBriefRuntime(mutate=mutate))
    assert job['state']=='failed' and brief(client,paper) is None


def test_live_job_readable_and_duplicate_start_rejected(client, paper):
    with closing(get_db()) as con:
        con.execute("INSERT INTO paper_brief_generations(id,source_id,state,snapshot_json,created_at,updated_at) VALUES('active',?,'generating','{}',?,?)",(paper,now(),now()))
        con.commit()
    assert client.get(f'/api/sources/{paper}/paper-brief/generation').json()['state']=='generating'
    assert client.post(f'/api/sources/{paper}/paper-brief/generate').status_code==409
    assert client.get(f'/api/sources/{paper}/file').status_code==200


def test_interrupted_job_expiry_and_no_same_origin_ai_control(client,paper):
    with closing(get_db()) as con:
        con.execute("INSERT INTO paper_brief_generations(id,source_id,state,snapshot_json,created_at,updated_at) VALUES('old',?,'generating','{}','2020-01-01T00:00:00+00:00','2020-01-01T00:00:00+00:00')",(paper,))
        con.commit()
    job=client.get(f'/api/sources/{paper}/paper-brief/generation').json()
    assert job['state']=='failed' and job['error']=='interrupted'
    res=client.post(f'/api/sources/{paper}/paper-brief/generate',headers={'Origin':'https://foreign.example'})
    assert res.status_code==403
    assert start(client,paper,FakeBriefRuntime())['state']=='preview'


def test_mid_generation_change_leaves_brief_untouched(client,paper):
    def mutate(d,c):
        if c['stage']=='classify':
            with closing(get_db()) as con:
                con.execute("UPDATE sources SET title='Changed during inference' WHERE id=?",(paper,))
                con.commit()
    job=start(client,paper,FakeBriefRuntime(mutate=mutate))
    assert job['state']=='failed' and job['error']=='source_changed'
    assert brief(client,paper) is None


def test_sqlite_commit_failure_rolls_back_all_fields(client,paper):
    job=start(client,paper,FakeBriefRuntime())
    with closing(get_db()) as con:
        con.execute("CREATE TRIGGER fail_generation BEFORE INSERT ON paper_brief_fields WHEN NEW.field_name='shots' BEGIN SELECT RAISE(FAIL,'injected'); END")
        con.commit()
    from app.routes_brief_generation import commit as commit_route, Confirm
    with pytest.raises(Exception,match='injected'):
        commit_route(paper,Confirm(generation_id=job['generation_id'],confirmed=True))
    assert brief(client,paper) is None
    assert client.get(f'/api/sources/{paper}/paper-brief/generation').json()['state']=='preview'


def test_json_duplicate_keys_and_nonfinite_rejected(client,paper):
    from app.ai.base import TurnEvent
    class Raw(FakeBriefRuntime):
        raw='{"paper_brief":{},"paper_brief":{}}'
        def send_turn(self,session,text,**kwargs):
            self.calls.append(text)
            yield TurnEvent('delta',session.id,'t',self.raw)
            yield TurnEvent('completed',session.id,'t')
    for raw in ('{"paper_brief":{},"paper_brief":{}}', '{"paper_brief":NaN}'):
        runtime=Raw(); runtime.raw=raw
        job=start(client,paper,runtime)
        assert job['state']=='failed' and len(runtime.calls)==2
    assert brief(client,paper) is None


def test_learning_vocabulary_is_not_conflated(client,paper):
    regimes=['zero-shot','one-shot','few-shot','many-shot','training-free','in-context learning',
        'supervised','self-supervised','full fine-tuning','fine-tuning','PEFT','LoRA','adapter',
        'prompt tuning','prompt learning','instruction tuning','distillation','frozen backbone']
    def mutate(d,c):
        if c['stage']=='extract': d['paper_brief']['learning_regimes']['value']=regimes
    job=start(client,paper,FakeBriefRuntime(mutate=mutate))
    assert commit(client,paper,job).status_code==200
    assert brief(client,paper)['fields']['learning_regimes']['value']==regimes


def test_generated_visual_contract_shared_with_kgpack_and_user_edit(client,paper):
    from tests.fixtures.kgpack_factory import package_data
    from app.import_bridge.schema import Package
    from app.import_bridge.validator import generate_package
    data=package_data()
    data['paper_brief']['important_figures']=field([{'label':'Figure 1','page':1,'caption':'Caption', 'evidence':['ev-unique']}],'confirmed',['ev-unique'])
    Package.model_validate(data)
    data['paper_brief']['important_figures']['value'][0]['evidence']=['missing']
    with pytest.raises(ValueError): Package.model_validate(data)
    job=start(client,paper,FakeBriefRuntime())
    assert commit(client,paper,job).status_code==200
    f=brief(client,paper)['fields']['important_figures']
    body=field(f['value'],'confirmed',f['evidence'])
    assert client.patch(f'/api/sources/{paper}/paper-brief/fields/important_figures',json=body).status_code==200


def test_missing_or_nonpdf_source_rejected(client):
    assert client.post('/api/sources/does-not-exist/paper-brief/generate').status_code==422
    source=client.post('/api/sources/text',json={'content':'Generated text only'}).json()['source']['id']
    assert client.post(f'/api/sources/{source}/paper-brief/generate').status_code==422


def test_preview_reopen_and_reading_activity_do_not_invalidate(client,paper):
    job=start(client,paper,FakeBriefRuntime())
    assert client.get(f'/api/sources/{paper}').status_code==200  # last_opened_at changes
    assert client.patch(f'/api/sources/{paper}',json={'reading_status':'reading'}).status_code==200
    assert client.get(f'/api/sources/{paper}/paper-brief/generation').json()['generation_id']==job['generation_id']
    assert commit(client,paper,job).status_code==200


def test_context_budget_includes_escaped_metadata_and_many_abstract_refs():
    blocks=[{'id':f'b{i}','idx':i,'kind':'para','text':'Short text','page':1,'bbox':None,
             'heading_path':'Abstract','level':None,'role':'para'} for i in range(1000)]
    doc,_=structure({'title':'\x01'*10000,'authors':['\x01'*200]*32,'year':None},{'id':'v'},blocks)
    for stage,limit in [('classify',CLASSIFY_BUDGET),('extract',EXTRACT_BUDGET)]:
        context,_=select_context(doc,stage=stage)
        assert len(canonical(context))<=limit
        assert context['coverage']['metadata_truncated']
        assert len(context['abstract_candidates'])<=8
