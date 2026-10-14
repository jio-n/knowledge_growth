"""Generic title meaning coverage, independent review, bounded retry and edits."""
from contextlib import closing
import json
import pytest
from app.db import get_db
from app.japanese_content import detect_units
from app.japanese_translation import translate_units,validate_block,TranslatedBlock,VERIFIED_TITLE_PROMPT
from app.japanese_title_review import validate_title_review,TitleReviewFailure
from app.routes_japanese_pdf import snapshot
from tests.test_japanese_overlap import job
from tests.fixtures.japanese_overlap_factory import (OverlapRuntime,title_review_reply,TITLE,TITLE_JA,TITLE_OMITTED,PARTS)
import fitz


def payload(target=TITLE_JA):return dict(block_id='title-fixture',original_title=TITLE,translated_title=target)


def test_missing_research_subject_rejected_even_with_all_hard_tokens_intact():
    # Counts, identifiers and Japanese generation alone cannot detect this loss.
    validate_block(TranslatedBlock(block_id='title-fixture',translated_text=TITLE_OMITTED,status='translated'),TITLE)
    p=payload(TITLE_OMITTED)
    with pytest.raises(TitleReviewFailure) as error:
        validate_title_review(json.dumps(title_review_reply(p)),p['block_id'],TITLE,TITLE_OMITTED)
    assert error.value.code=='title_incomplete'
    assert error.value.report['status']=='incomplete'
    assert next(c for c in error.value.report['checks'] if c['category']=='research_subject')['status']=='missing'


def test_complete_title_requires_all_five_categories_and_source_token_attribution():
    p=payload();raw=json.dumps(title_review_reply(p))
    for response in (raw,'```json\n'+raw+'\n```'):
        result=validate_title_review(response,p['block_id'],TITLE,TITLE_JA)
        assert result['status']=='complete' and result['unmapped_source_tokens']==0 and len(result['checks'])==5
        assert TITLE not in json.dumps(result) and TITLE_JA not in json.dumps(result,ensure_ascii=False)


@pytest.mark.parametrize('mode,code',[
    ('malformed','title_review_invalid'),('wrong_id','title_review_invalid'),
    ('missing_check','title_review_invalid'),('fabricated_range','title_review_invalid'),
    ('unmapped','title_incomplete'),('uncertain','title_review_uncertain'),('timeout','ai_timeout')])
def test_review_failures_only_retry_title_and_preserve_peer_success(client,mode,code):
    _,source,version,_,units=job(client);runtime=OverlapRuntime(audit_mode=mode)
    stats=translate_units(runtime,source,version,units)
    title=next(u for u in units if u.get('semantic_role')=='paper_title')
    assert not title.get('translation_succeeded') and title['status']=='failed'
    assert stats['attempt_errors'][code]>=1 and stats['runtime_calls']<=48 and runtime.reviews<=4
    assert len([b for b in runtime.inputs if b['text']==PARTS[0]])==1
    assert all(u['translation_succeeded'] for u in units if u.get('semantic_role')=='abstract_body')
    with closing(get_db()) as con:
        assert con.execute('SELECT count(*) FROM translations WHERE source_id=? AND translated_text<>?',(source['id'],'')).fetchone()[0]>=7
    assert TITLE not in json.dumps(stats) and TITLE_JA not in json.dumps(stats,ensure_ascii=False)


def test_title_omission_retries_title_only_then_reuses_successful_body(client):
    sid,source,version,_,units=job(client);runtime=OverlapRuntime(omit_title=True)
    stats=translate_units(runtime,source,version,units)
    title=next(u for u in units if u.get('semantic_role')=='paper_title')
    assert title['translation_error']=='title_incomplete' and stats['attempt_errors']['title_incomplete']>=1
    assert runtime.reviews>=2 and [b['text'] for b in runtime.inputs].count(PARTS[0])==1
    runtime.omit_title=False;runtime.inputs.clear()
    with closing(get_db()) as con:source,version,blocks,data,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as doc:units=detect_units(doc,blocks,[1,2])
    translate_units(runtime,source,version,units)
    title=next(u for u in units if u.get('semantic_role')=='paper_title')
    assert title['translation_succeeded'] and title['prompt_version']==VERIFIED_TITLE_PROMPT
    assert all(b['text'] not in PARTS for b in runtime.inputs)


@pytest.mark.parametrize('edited',[False,True])
def test_legacy_cached_title_review_cannot_rewrite_user_edits(client,edited):
    sid,source,version,_,units=job(client);translate_units(OverlapRuntime(),source,version,units)
    title=next(u for u in units if u.get('semantic_role')=='paper_title')
    with closing(get_db()) as con:
        con.execute('UPDATE translations SET translated_text=?,user_edited=?,prompt_version=? WHERE block_id=?',
                    (TITLE_OMITTED,int(edited),'japanese-pdf-0.4',title['block']['id']));con.commit()
        original=dict(con.execute('SELECT * FROM translations WHERE block_id=?',(title['block']['id'],)).fetchone())
        source,version,blocks,data,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as doc:units=detect_units(doc,blocks,[1,2])
    runtime=OverlapRuntime();stats=translate_units(runtime,source,version,units)
    t=next(u for u in units if u.get('semantic_role')=='paper_title')
    assert t['translation_succeeded']
    if edited:
        assert runtime.reviews==0 and t['text']==TITLE_OMITTED and t['title_review']['status']=='user_edited'
        with closing(get_db()) as con:assert dict(con.execute('SELECT * FROM translations WHERE id=?',(original['id'],)).fetchone())==original
    else:
        assert stats['attempt_errors']['title_incomplete']==1 and runtime.reviews==2 and t['text']==TITLE_JA
        assert all(b['text'] not in PARTS for b in runtime.inputs)


def test_verified_title_cache_needs_no_further_ai_review(client):
    sid,source,version,_,units=job(client);translate_units(OverlapRuntime(),source,version,units)
    with closing(get_db()) as con:source,version,blocks,data,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as doc:units=detect_units(doc,blocks,[1,2])
    runtime=OverlapRuntime();translate_units(runtime,source,version,units)
    t=next(u for u in units if u.get('semantic_role')=='paper_title')
    assert t['translation_succeeded'] and t['cached'] and t['title_review']['reused'] and runtime.reviews==0


def test_review_uses_global_runtime_budget(client,monkeypatch):
    _,source,version,_,units=job(client)
    monkeypatch.setattr('app.japanese_translation.MAX_RUNTIME_CALLS',1)
    stats=translate_units(OverlapRuntime(),source,version,units)
    assert stats['runtime_calls']==1 and stats['stopped_by']=='translation_attempt_limit'
    assert not next(u for u in units if u.get('semantic_role')=='paper_title').get('translation_succeeded')


@pytest.mark.parametrize('mutation',[
    'duplicate_category','extra_key','non_integer_range','empty_span','false_not_applicable','duplicate_json_key'])
def test_review_schema_does_not_accept_fabricated_evidence(mutation):
    p=payload();answer=title_review_reply(p)
    if mutation=='duplicate_category':answer['checks'][1]['category']='research_subject'
    if mutation=='extra_key':answer['private']='NEVER LOG'
    if mutation=='non_integer_range':answer['checks'][0]['source_spans'][0]['start']=False
    if mutation=='empty_span':answer['checks'][0]['translated_spans']=[]
    if mutation=='false_not_applicable':answer['checks'][0].update(status='not_applicable',source_spans=[],translated_spans=[])
    raw=json.dumps(answer)
    if mutation=='duplicate_json_key':raw=raw[:-1]+',"verdict":"complete"}'
    with pytest.raises(TitleReviewFailure) as error:validate_title_review(raw,p['block_id'],TITLE,TITLE_JA)
    assert error.value.code=='title_review_invalid'
