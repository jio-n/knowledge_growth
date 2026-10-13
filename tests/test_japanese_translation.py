"""Independent validation, bounded isolation and safe diagnostics with fake AI."""
from contextlib import closing
import json
import time
import fitz
import pytest
from app.ai.compat import RuntimeProvider, RuntimeLLMError
from app.db import get_db
from app.japanese_translation import (translate_units, validate_reply, validate_batch,
    TranslationFailure, TRANSLATION_ERRORS)
from app.routes_japanese_pdf import snapshot
from tests.fixtures.japanese_pdf_factory import make_translation_diagnostic_fixture as synthetic_pdf
from tests.fixtures.japanese_translation_cases import TITLE, PARAGRAPH, CAPTION, TRANSLATIONS, TranslationFaultRuntime
from tests.test_japanese_pdf import upload, generate


def unit_job(client, texts):
    sid=upload(client,synthetic_pdf())
    with closing(get_db()) as con:
        source, version, blocks, _, _ = snapshot(con,sid)
    template=next(b for b in blocks if b['text']==PARAGRAPH)
    units=[]
    for i,text in enumerate(texts):
        b={**template,'id':f'synthetic-{i}','text':text}
        units.append({'block':b,'status':'pending','reason':None,'kind':'text','asset_id':None})
    return source,version,units


def reply(original,translated,*,status='translated'):
    return json.dumps({'blocks':[{'block_id':'x','translated_text':translated,'status':status}]}),[{'id':'x','text':original}]


@pytest.mark.parametrize('original,translated',list(TRANSLATIONS.items())+[
    ('Accuracy 91.012345% [1,2,7]. Table 2 and Figure 1.', '正解率91.012345 % [1, 2, 7]。表2と図1。'),
    ('GPT-4o uses 1.2e-3 and -0.50 in Equation (1).', 'GPT-4oは式(1)で1.2e-3と-0.50を使用する。'),
    ('Version 0.160.1 cites [12-14].', 'バージョン0.160.1は[12-14]を引用する。'),
    ('A state-of-the-art multi-modal method.', '最先端のマルチモーダル手法。'),
    ('We use 1,024 samples [1,234].', '1,024個の標本を使う[1, 234]。'),
    ('Accuracy 91.0% [12].', '正解率91.0\n% [12]。'),
])
def test_valid_synthetic_identifiers_numbers_and_citations_are_not_falsely_rejected(original,translated):
    text,batch=reply(original,translated)
    assert validate_reply(text,batch).blocks[0].translated_text==translated


@pytest.mark.parametrize('original,translated,code',[
    ('Accuracy 91.012345%.','正解率91.0%。','numbers_changed'),
    ('Value -0.50.','値0.50。','numbers_changed'),
    ('Rate 1.2e-3.','値1.2e+3。','numbers_changed'),
    ('We use 1,024 samples.','標本1と024を使う。','numbers_changed'),
    ('We use 1,024 samples.','標本1024を使う。','numbers_changed'),
    ('References [1, 2, 7].','引用[7, 2, 1]。','citations_changed'),
    ('Figure 1 and Table 2.','図2と表1。','references_changed'),
    ('GPT-4o on MMAD.','MMADのGPT-4a。','terms_changed'),
    ('GPT4o on MMAD.','MMADのGPT4a。','terms_changed'),
    ('A paragraph.','An English paragraph.','japanese_required'),
])
def test_validation_rejects_actual_content_changes(original,translated,code):
    text,batch=reply(original,translated)
    with pytest.raises(TranslationFailure) as error: validate_reply(text,batch)
    assert error.value.code==code and str(error.value)==code


@pytest.mark.parametrize('text,code',[
    ('not JSON SECRET','invalid_json'),
    ('{"blocks":[],"blocks":[]}','invalid_json'),
    ('{"blocks":[NaN]}','invalid_json'),
    ('```json\n{"blocks":[]}\n``` trailing prose','invalid_json'),
    ('Here is the JSON: {"blocks":[]}','invalid_json'),
    ('[]','schema_mismatch'),
    ('{"blocks":{},"secret":"RAW"}','schema_mismatch'),
    ('x'*128001,'output_too_large'),
])
def test_whole_reply_errors_are_classified_without_payload(text,code):
    with pytest.raises(TranslationFailure) as error: validate_batch(text,[{'id':'x','text':TITLE}])
    assert str(error.value)==code


@pytest.mark.parametrize('mode,code',[
    ('numbers','numbers_changed'),('uncertain','ai_uncertain'),('english','japanese_required'),
    ('schema','schema_mismatch'),('missing','block_id_mismatch'),
    ('duplicate','block_id_mismatch'),('invented','block_id_mismatch'),
])
def test_one_bad_block_preserves_successful_peers_and_retries_only_that_id(client,mode,code):
    source,version,units=unit_job(client,[TITLE,PARAGRAPH,CAPTION])
    runtime=TranslationFaultRuntime(mode)
    stats=translate_units(runtime,source,version,units)
    assert len(runtime.inputs)==2
    assert [b['block_id'] for b in runtime.inputs[1]['blocks']]==[units[1]['block']['id']]
    assert units[0]['translation_succeeded'] and units[2]['translation_succeeded']
    assert units[1]['status']=='failed' and units[1]['reason']==code
    assert stats['runtime_calls']==2
    with closing(get_db()) as con:
        saved=con.execute('SELECT * FROM translations WHERE source_id=?',(source['id'],)).fetchall()
    assert len(saved)==2 and {r['translated_text'] for r in saved}=={TRANSLATIONS[TITLE],TRANSLATIONS[CAPTION]}
    # Another generation with the corrected runtime uses both saved successes.
    recovered=TranslationFaultRuntime('valid')
    for u in units: u.update(status='pending',reason=None);u.pop('translation_succeeded',None)
    translate_units(recovered,source,version,units)
    assert len(recovered.inputs)==1 and len(recovered.inputs[0]['blocks'])==1
    assert all(u['translation_succeeded'] for u in units)


def test_broken_json_is_split_until_singletons_and_successes_are_saved(client):
    source,version,units=unit_job(client,[TITLE,PARAGRAPH,CAPTION])
    runtime=TranslationFaultRuntime('split')
    stats=translate_units(runtime,source,version,units)
    sizes=[len(c['blocks']) for c in runtime.inputs]
    assert sizes==[3,3,1,2,2,1,1]
    assert stats['split_batches']==2 and all(u['translation_succeeded'] for u in units)
    assert stats['attempt_errors']['invalid_json']==10


@pytest.mark.parametrize('mode,code',[('timeout','ai_timeout'),('incomplete','ai_incomplete_turn'),
    ('rpc','ai_rpc_error'),('runtime','ai_runtime_error'),('cancelled','ai_cancelled'),('bad_json','invalid_json')])
def test_runtime_and_json_failures_are_safe_and_bounded(client,mode,code,caplog,capsys):
    runtime=TranslationFaultRuntime(mode);client.app.state.ai_runtime=runtime
    data=synthetic_pdf();sid=upload(client,data)
    status,response=generate(client,sid,[1])
    d=status['report']['diagnostics']
    assert status['state']=='failed' and status['error']=='no_safe_translated_blocks' and response.status_code==404
    assert d['failure_stage']=='translation'
    assert d['translation_candidates']==3 and d['translation_succeeded']==d['pdf_placement_succeeded']==0
    assert d['translation_failures']=={code:3} and d['protected_units']==3
    assert 2 < d['runtime_calls'] <= 48
    assert client.get(f'/api/sources/{sid}/file').content==data
    observed=json.dumps(status)+caplog.text+capsys.readouterr().out
    assert 'SECRET_RESPONSE' not in observed and 'SECRET_CREDENTIAL' not in observed
    assert TITLE not in observed and TRANSLATIONS[TITLE] not in observed
    with closing(get_db()) as con:
        assert all(row['reason']==code for row in con.execute("SELECT reason FROM translations WHERE source_id=? AND translation_status='failed'",(sid,)))


def test_partial_success_produces_pdf_and_distinct_ai_layout_diagnostics(client):
    runtime=TranslationFaultRuntime('numbers');client.app.state.ai_runtime=runtime
    sid=upload(client,synthetic_pdf());status,response=generate(client,sid,[1])
    assert status['state']=='preview_ready',status
    d=status['report']['diagnostics']
    assert d['translation_candidates']==3 and d['ai_translation_succeeded']==2 and d['pdf_placement_succeeded']==2
    assert d['translation_failures']=={'numbers_changed':1} and d['protected_units']==3
    with fitz.open(stream=response.content,filetype='pdf') as doc:
        text=''.join(p.get_text() for p in doc)
        assert TRANSLATIONS[TITLE] in text and '91.012345' in text and 'y = W x + b (1)' in text
    # Retry keeps successes and requests only the formerly bad original paragraph.
    runtime.mode='valid'
    status,_=generate(client,sid,[1],retry_failed=True)
    assert status['state']=='preview_ready' and status['report']['diagnostics']['cached_translations']==2
    assert len(runtime.inputs[-1]['blocks'])==1


@pytest.mark.parametrize('mode',['numbers_once','uncertain_once','schema_once','fenced'])
def test_real_ai_style_chunked_json_and_recoverable_rows(client,mode):
    source,version,units=unit_job(client,[TITLE,PARAGRAPH,CAPTION])
    runtime=TranslationFaultRuntime(mode)
    translate_units(runtime,source,version,units)
    assert all(u['translation_succeeded'] for u in units)
    assert len(runtime.inputs)==(1 if mode=='fenced' else 2)


def test_global_call_budget_survives_splits_without_overwriting_specific_reason(client,monkeypatch):
    import app.japanese_translation as translation
    monkeypatch.setattr(translation,'MAX_RUNTIME_CALLS',3)
    source,version,units=unit_job(client,[TITLE,PARAGRAPH,CAPTION])
    runtime=TranslationFaultRuntime('split')
    stats=translate_units(runtime,source,version,units)
    assert stats['runtime_calls']==3 and stats['stopped_by']=='translation_attempt_limit'
    assert units[0]['translation_succeeded']
    assert units[1]['reason']==units[2]['reason']=='invalid_json'


def test_time_budget_stops_calls_preserves_committed_success_and_specific_failure(client,monkeypatch):
    import app.japanese_translation as translation
    source,version,units=unit_job(client,[TITLE,PARAGRAPH,CAPTION])
    runtime=TranslationFaultRuntime('numbers')
    class Clock:
        value=time.monotonic()
        def monotonic(self): return self.value
    clock=Clock();monkeypatch.setattr(translation,'time',clock)
    send=runtime.send_turn
    def elapsed(*args,**kwargs):
        yield from send(*args,**kwargs)
        clock.value+=601
    monkeypatch.setattr(runtime,'send_turn',elapsed)
    stats=translate_units(runtime,source,version,units)
    assert stats['runtime_calls']==1 and stats['stopped_by']=='translation_time_limit'
    assert units[0]['translation_succeeded'] and units[2]['translation_succeeded']
    assert units[1]['reason']=='numbers_changed'


def test_unknown_and_duplicate_ids_cannot_pollute_valid_unit(client):
    text=json.dumps({'blocks':[
        {'block_id':'x','translated_text':TRANSLATIONS[TITLE],'status':'translated'},
        {'block_id':'foreign','translated_text':'秘密の日本語','status':'translated'}]})
    good,bad,anomaly=validate_batch(text,[{'id':'x','text':TITLE}])
    assert set(good)=={'x'} and bad=={} and anomaly=='block_id_mismatch'
    with pytest.raises(TranslationFailure): validate_reply(text,[{'id':'x','text':TITLE}])


def test_runtime_adapter_preserves_error_category_without_untrusted_message():
    runtime=TranslationFaultRuntime('runtime')
    with pytest.raises(RuntimeLLMError) as error:
        RuntimeProvider(runtime).complete_json('# japanese-pdf-0.2',json.dumps({'blocks':[]}))
    assert 'SECRET' not in str(error.value)


def test_per_unit_budget_bounds_recursive_splits(client,monkeypatch):
    import app.japanese_translation as translation
    monkeypatch.setattr(translation,'MAX_UNIT_ATTEMPTS',3)
    source,version,units=unit_job(client,[TITLE]*8)
    runtime=TranslationFaultRuntime('bad_json')
    stats=translate_units(runtime,source,version,units)
    assert all(u['translation_attempts']==3 and u['reason']=='invalid_json' for u in units)
    assert stats['runtime_calls']==4


def test_unattempted_blocks_get_safe_global_limit_code(client,monkeypatch):
    import app.japanese_translation as translation
    monkeypatch.setattr(translation,'MAX_RUNTIME_CALLS',0)
    source,version,units=unit_job(client,[TITLE])
    stats=translate_units(TranslationFaultRuntime(),source,version,units)
    assert stats['runtime_calls']==0 and units[0]['reason']=='translation_attempt_limit'


def test_layout_failure_reports_successful_translation_separately(client,monkeypatch):
    import app.routes_japanese_pdf as routes
    from app.japanese_translation import PDFError
    client.app.state.ai_runtime=TranslationFaultRuntime()
    sid=upload(client,synthetic_pdf())
    def fail(*args): raise PDFError('layout_overlap')
    monkeypatch.setattr(routes,'generate_pdf',fail)
    status,_=generate(client,sid,[1])
    d=status['report']['diagnostics']
    assert d['failure_stage']=='pdf_layout' and d['translation_succeeded']==3
    assert d['pdf_placement_succeeded']==0 and d['translation_failures']=={}


def test_partial_success_and_failures_persist_across_app_restart(client):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.ai.offline import NoAIRuntime
    client.app.state.ai_runtime=TranslationFaultRuntime('numbers')
    sid=upload(client,synthetic_pdf());status,pdf=generate(client,sid,[1])
    with TestClient(create_app(runtime=NoAIRuntime())) as restarted:
        saved=restarted.get(f'/api/sources/{sid}/japanese-pdf').json()
        assert saved['report']['diagnostics']==status['report']['diagnostics']
        assert restarted.get(f'/api/sources/{sid}/japanese-pdf/{saved["generation_id"]}/file').content==pdf.content


def test_retry_updates_prompt_provenance_on_old_failed_row_without_duplicate_storage(client):
    from app.japanese_translation import save_unit, PROMPT_VERSION
    source,version,units=unit_job(client,[TITLE])
    unit=units[0];unit.update(status='failed',reason='translation_failed',prompt_version='japanese-pdf-0.1')
    with closing(get_db()) as con:
        save_unit(con,source,version,unit);con.commit()
    unit.update(status='pending',reason=None)
    translate_units(TranslationFaultRuntime(),source,version,units)
    with closing(get_db()) as con:
        rows=con.execute('SELECT * FROM translations WHERE source_id=?',(source['id'],)).fetchall()
    assert len(rows)==1 and rows[0]['prompt_version']==PROMPT_VERSION
    assert rows[0]['translated_text']==TRANSLATIONS[TITLE]
