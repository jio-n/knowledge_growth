"""Reader-quality acceptance with synthetic paper data, no ChatGPT login."""
from contextlib import closing
import json
import fitz
import pytest
from fastapi.testclient import TestClient
from app.ai.offline import NoAIRuntime
from app.main import create_app
from app.db import get_db
from app.japanese_pdf import norm
from app.japanese_semantics import restore_line_words
from tests.test_japanese_pdf import upload,generate
from tests.fixtures.japanese_semantic_factory import (make_semantic_fixture,SemanticRuntime,JA,TITLE,
    TITLE_LINES,ABSTRACT_BODY,ABSTRACT_SECOND,UNTRANSLATED,INTRO,ARXIV,CONFERENCE)


@pytest.mark.parametrize('original,expected',[
 ('MUL-\nTIMODAL','MULTIMODAL'),('classifi-\ncation','classification'),
 ('Zero-\nshot','Zero-shot'),('Few-shot','Few-shot'),('Vision-\nLanguage','Vision-Language'),
 ('GPT-\n4o','GPT-4o'),('Synthetic-\nUnknown','Synthetic-Unknown')])
def test_line_word_restoration_preserves_compounds_and_identifiers(original,expected):
    assert restore_line_words(original)==expected


def test_semantic_paper_has_complete_title_abstract_reading_order_and_provenance(client,caplog,capsys):
    runtime=SemanticRuntime();client.app.state.ai_runtime=runtime
    original=make_semantic_fixture();sid=upload(client,original)
    doc_before=client.get(f'/api/sources/{sid}/document').json()
    status,response=generate(client,sid,[1,2,3])
    assert status['state']=='preview_ready',status
    r=status['report'];assert r['assembly_version']=='semantic-1' and r['layout_version']=='reflow-3'
    title=next(u for u in r['semantic_units'] if u['role']=='paper_title')
    assert len(title['original_block_ids'])==3 and title['status']=='translated'
    requests=[b for turn in runtime.inputs for b in turn]
    assert sum(b['text']==TITLE for b in requests)==1
    assert not any(b['text'] in TITLE_LINES for b in requests)
    assert next(b for b in requests if b['text']==TITLE)['role']=='paper_title'
    assert sum(b['text']==ABSTRACT_BODY for b in requests)==1
    assert r['coverage']['title']=={'units':1,'source_blocks':3,'complete':True,'translated':True}
    assert r['coverage']['abstract_complete'] and r['coverage']['abstract']['ratio']==1
    assert r['coverage']['body']['translated_source_chars']==630
    assert r['coverage']['body']['source_chars']==773
    assert r['coverage']['body_preserved_in_visuals']==0
    assert {u['block_id'] for u in r['units']}=={b['id'] for b in doc_before['blocks']}
    assert len(r['units'])==len(doc_before['blocks'])==r['quality']['source_blocks_mapped']
    assert r['quality']['source_mapping_duplicates']==0
    assert all('spans' not in (u['bbox'] or {}) for u in r['units'])
    assert not any(a['kind']=='original_text' for a in r['assets'])
    assert {'figure','table','equation'}=={a['kind'] for a in r['assets']}
    assert all(q['status']=='passed' and q['drawings_preserved'] and q['images_preserved'] for q in r['quality']['visuals'])
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=''.join(p.get_text() for p in pdf);normalized=norm(text)
        assert normalized.count(norm(JA[TITLE]))==1
        assert all(norm(line) not in normalized for line in TITLE_LINES)
        assert normalized.count(norm(JA[ABSTRACT_BODY]))==normalized.count(norm(JA[ABSTRACT_SECOND]))==1
        assert normalized.count(norm(UNTRANSLATED))==1 and '原文（未翻訳）' in text
        assert '未解決・原物保持' not in text
        assert text.index('Ada Example 1 Ben Example 2')<text.index('要旨')
        assert text.index(ARXIV)<text.index('要旨') and text.index(CONFERENCE)<text.index('要旨')
        assert text.index('原文の周辺情報')>text.index(JA[INTRO[-1]])
        assert [normalized.index(norm(JA[s])) for s in INTRO]==sorted(normalized.index(norm(JA[s])) for s in INTRO)
        for value in ('91.012345','92.987654','0.123456','0.345678','GPT-4o','ViT-B/16','y = W x + b (1)'):
            assert value in text
        for asset in r['assets']:
            for bid in asset['caption_block_ids']:
                row=next(u for u in r['units'] if u['block_id']==bid)
                assert all(p['japanese_page']==asset['japanese_page'] for p in row['placements'])
        fallback=next(u for u in r['units'] if u['reason']=='ai_uncertain')
        assert fallback['font_size']==11 and fallback['untranslated']
    assert client.get(f'/api/sources/{sid}/file').content==original
    doc_after=client.get(f'/api/sources/{sid}/document').json()
    assert doc_after['blocks']==doc_before['blocks'] and doc_after['version']==doc_before['version']
    safe=json.dumps(r,ensure_ascii=False)+caplog.text+capsys.readouterr().out
    assert TITLE not in safe and ABSTRACT_BODY not in safe and JA[TITLE] not in safe


def test_title_failure_retains_whole_original_once_and_success_cache_restores(client):
    runtime=SemanticRuntime(fail_title=True);client.app.state.ai_runtime=runtime
    sid=upload(client,make_semantic_fixture());first,response=generate(client,sid,[1])
    assert first['state']=='preview_ready'
    title=next(u for u in first['report']['semantic_units'] if u['role']=='paper_title')
    assert title['reason']=='ai_uncertain' and len(title['original_block_ids'])==3
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=norm(''.join(p.get_text() for p in pdf))
        assert text.count(norm(TITLE))==1 and norm(JA[TITLE]) not in text
    saved=first['report']['diagnostics']['translation_succeeded']
    runtime.fail_title=False
    second,_=generate(client,sid,[1],retry_failed=True)
    assert second['state']=='preview_ready'
    assert second['report']['diagnostics']['cached_translations']==saved
    assert second['report']['coverage']['title']['translated']
    with TestClient(create_app(runtime=NoAIRuntime())) as restarted:
        assert restarted.get(f'/api/sources/{sid}/japanese-pdf').json()['state']=='preview_ready'
        assert restarted.get(f'/api/sources/{sid}/japanese-pdf/{second["generation_id"]}/file').status_code==200


def seed_title_parts(client,sid,*,user=False):
    doc=client.get(f'/api/sources/{sid}/document').json()
    parts=[b for b in doc['blocks'] if b['text'] in TITLE_LINES]
    with closing(get_db()) as con:
        for i,b in enumerate(parts):
            con.execute('''INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,user_edited,provider,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'user','old','old')''',(f'legacy-part-{i}',sid,doc['version']['id'],b['id'],b['text'],'古い部分訳',int(user and i==0)))
        con.commit()
        return [dict(r) for r in con.execute("SELECT * FROM translations WHERE id LIKE 'legacy-part-%' ORDER BY id")]


@pytest.mark.parametrize('user',[False,True])
def test_partial_title_translations_are_not_misapplied_or_overwritten(client,user):
    runtime=SemanticRuntime();client.app.state.ai_runtime=runtime
    sid=upload(client,make_semantic_fixture());before=seed_title_parts(client,sid,user=user)
    status,response=generate(client,sid,[1])
    assert status['state']=='preview_ready'
    with closing(get_db()) as con:
        assert [dict(r) for r in con.execute("SELECT * FROM translations WHERE id LIKE 'legacy-part-%' ORDER BY id")]==before
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=norm(''.join(p.get_text() for p in pdf));assert '古い部分訳' not in text
        assert norm(TITLE if user else JA[TITLE]) in text
    title=next(u for u in status['report']['semantic_units'] if u['role']=='paper_title')
    assert title['id'].startswith('S') and len(title['id'])==64
    assert title['reason']==('assembled_user_edit_review' if user else None)
    if user:assert not any(b['text']==TITLE for turn in runtime.inputs for b in turn)


def test_edited_whole_semantic_title_is_reused_with_full_original_mapping(client):
    client.app.state.ai_runtime=SemanticRuntime();sid=upload(client,make_semantic_fixture())
    first,_=generate(client,sid,[1]);title=next(u for u in first['report']['semantic_units'] if u['role']=='paper_title')
    edited='ユーザーが編集した全文の研究タイトル'
    with closing(get_db()) as con:
        con.execute('UPDATE translations SET translated_text=?,user_edited=1 WHERE id=?',(edited,title['translation_id']));con.commit()
    again,response=generate(client,sid,[1],retry_failed=True)
    assert again['state']=='preview_ready'
    with fitz.open(stream=response.content,filetype='pdf') as pdf:assert norm(edited) in norm(''.join(p.get_text() for p in pdf))
    assert next(u for u in again['report']['semantic_units'] if u['role']=='paper_title')['original_block_ids']==title['original_block_ids']


def test_later_partial_user_edit_blocks_earlier_cached_whole_title_without_rewriting_either(client):
    client.app.state.ai_runtime=SemanticRuntime();sid=upload(client,make_semantic_fixture())
    first,_=generate(client,sid,[1]);title=next(u for u in first['report']['semantic_units'] if u['role']=='paper_title')
    before=seed_title_parts(client,sid,user=True)
    with closing(get_db()) as con:whole=dict(con.execute('SELECT * FROM translations WHERE id=?',(title['translation_id'],)).fetchone())
    status,response=generate(client,sid,[1],retry_failed=True)
    assert status['state']=='preview_ready'
    assert next(u for u in status['report']['semantic_units'] if u['role']=='paper_title')['reason']=='assembled_user_edit_review'
    with closing(get_db()) as con:
        assert [dict(r) for r in con.execute("SELECT * FROM translations WHERE id LIKE 'legacy-part-%' ORDER BY id")]==before
        assert con.execute('SELECT translated_text FROM translations WHERE id=?',(title['translation_id'],)).fetchone()[0]==whole['translated_text']
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=norm(''.join(p.get_text() for p in pdf));assert norm(TITLE) in text and norm(JA[TITLE]) not in text


def test_metadata_reclassification_preserves_earlier_successful_translation_row(client):
    client.app.state.ai_runtime=SemanticRuntime();sid=upload(client,make_semantic_fixture())
    doc=client.get(f'/api/sources/{sid}/document').json()
    affiliation=next(b for b in doc['blocks'] if 'Synthetic Institute' in b['text'])
    with closing(get_db()) as con:
        con.execute('''INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,provider,created_at,updated_at)
            VALUES('metadata-old',?,?,?,?,?,'mock','old','old')''',(sid,doc['version']['id'],affiliation['id'],affiliation['text'],'既存の所属訳'))
        con.commit();before=dict(con.execute("SELECT * FROM translations WHERE id='metadata-old'").fetchone())
    status,_=generate(client,sid,[1]);assert status['state']=='preview_ready'
    with closing(get_db()) as con:assert dict(con.execute("SELECT * FROM translations WHERE id='metadata-old'").fetchone())==before
