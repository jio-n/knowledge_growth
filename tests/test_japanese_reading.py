"""TeX-like Abstract extraction, whitespace and reading-quality acceptance."""
from contextlib import closing
import json
import re
import fitz
import pytest
from fastapi.testclient import TestClient
from app.ai.offline import NoAIRuntime
from app.db import get_db
from app.main import create_app
from app.japanese_content import detect_units
from app.japanese_pdf import norm
from app.japanese_translation import validate_block,TranslatedBlock,TranslationFailure
from app.japanese_extraction import recover_text,glyph_lines
from app.japanese_semantics import coverage
from app.ingest.pdf import extract_pdf
from tests.test_japanese_pdf import upload,generate
from tests.fixtures.japanese_reading_factory import (make_reading_fixture,ReadingRuntime,ABSTRACT,FALLBACK,TITLE,FOOTNOTE,JA,INTRO,cm_font,words)


def test_conference_abstract_is_translated_not_cropped_and_source_is_immutable(client,caplog,capsys):
    original=make_reading_fixture();runtime=ReadingRuntime();client.app.state.ai_runtime=runtime
    sid=upload(client,original);before=client.get(f'/api/sources/{sid}/document').json()
    # Reproduce the actual extraction bug, not just a manufactured bad string.
    source_abstract=next(b for b in before['blocks'] if b['text'].startswith('Nowadays') and '91.012345' in b['text'])
    assert 'multimodallargelanguage' in source_abstract['text']
    status,response=generate(client,sid,[1,2]);assert status['state']=='preview_ready',status
    report=status['report'];abstract=report['coverage']['abstract'];size=len(norm(ABSTRACT))
    assert abstract['source_chars']==size
    assert abstract['candidate_source_chars']==abstract['translated_source_chars']==abstract['placed_source_chars']==abstract['placed_translated_source_chars']==size
    assert abstract['protected_source_chars']==0 and abstract['untranslated_reasons']=={}
    assert report['reading_quality']=={'pdf_generated':True,'abstract':'complete'}
    assert report['coverage']['title']['translated'] and report['coverage']['body_preserved_in_visuals']==0
    assert [b['text'] for b in runtime.inputs].count(ABSTRACT)==1
    assert not any(b['text'] in (FOOTNOTE,'1','2') for b in runtime.inputs)
    assert {a['kind'] for a in report['assets']}=={'figure','table','equation'}
    assert all(q['status']=='passed' and q['vector_preserved'] for q in report['quality']['visuals'])
    abstract_unit=next(u for u in report['semantic_units'] if u['role']=='abstract_body')
    assert abstract_unit['extraction']['inferred_spaces']>10 and abstract_unit['extraction']['glyph_text_matched']
    if cm_font().with_name('cmmi10.ttf').exists():assert abstract_unit['extraction']['math_font_runs']>0
    foot=next(u for u in report['semantic_units'] if u['role']=='footnote')
    assert foot['status']=='skipped_protected'
    assert len([u for u in report['semantic_units'] if u['role']=='footer'])==2
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        texts=[p.get_text() for p in pdf];text='\n'.join(texts)
        assert norm(text).count(norm(JA[ABSTRACT]))==1
        assert norm(text).count(norm(JA[TITLE]))==1
        assert norm(ABSTRACT) not in norm(text)
        assert re.sub(r'\s+',' ',FALLBACK) in re.sub(r'\s+',' ',text)
        assert FOOTNOTE in text and '脚注（原文）' in text
        assert 'multimodallargelanguage' not in text and '未解決・原物保持' not in text
        assert [text.index(JA[s]) for s in INTRO]==sorted(text.index(JA[s]) for s in INTRO)
        assert 'GPT-4o' in text and 'ViT-B/16' in text and '91.012345' in text
        for number in range(1,len(pdf)+1):
            # A short genuine equation/visual is allowed; end matter alone may
            # not manufacture a sparse extra page for this fixture.
            assert any(u['kind']!='metadata' and any(p['japanese_page']==number for p in u['placements']) for u in report['units'])
        for u in report['units']:
            if u['kind']=='metadata' and u['font_size'] is not None:assert u['font_size']==11
        assert all(u['semantic_role']=='footer' for u in report['units'] if u['block_id'] in [b['id'] for b in before['blocks'] if b['text'] in ('1','2')])
    assert client.get(f'/api/sources/{sid}/file').content==original
    assert client.get(f'/api/sources/{sid}/document').json()['blocks']==before['blocks']
    safe=json.dumps(report,ensure_ascii=False)+caplog.text+capsys.readouterr().out
    assert ABSTRACT not in safe and JA[ABSTRACT] not in safe and source_abstract['text'] not in safe
    with TestClient(create_app(runtime=NoAIRuntime())) as restarted:
        assert restarted.get(f'/api/sources/{sid}/japanese-pdf/{status["generation_id"]}/file').content==response.content


def test_untranslated_abstract_is_readable_text_and_incomplete_not_success(client):
    runtime=ReadingRuntime(fail_abstract=True);client.app.state.ai_runtime=runtime
    sid=upload(client,make_reading_fixture());status,res=generate(client,sid,[1]);assert status['state']=='preview_ready',status
    a=status['report']['coverage']['abstract'];size=len(norm(ABSTRACT))
    assert a['source_chars']==a['candidate_source_chars']==a['placed_source_chars']==size
    assert a['translated_source_chars']==a['protected_source_chars']==a['placed_translated_source_chars']==0
    assert a['untranslated_reasons']=={'ai_uncertain':size}
    assert not status['report']['coverage']['abstract_complete']
    assert status['report']['reading_quality']=={'pdf_generated':True,'abstract':'incomplete'}
    assert not status['report']['assets']
    with fitz.open(stream=res.content,filetype='pdf') as pdf:
        text=' '.join(p.get_text() for p in pdf)
        # A typographic fi glyph has the same two-letter logical text.
        assert ABSTRACT in re.sub(r'\s+',' ',text.replace('ﬁ','fi')) and '原文（未翻訳）' in text
        assert len(pdf)==1
    runtime.fail_abstract=False;runtime.inputs=[]
    again,_=generate(client,sid,[1],retry_failed=True)
    assert again['report']['reading_quality']['abstract']=='complete'
    assert set(b['text'] for b in runtime.inputs)=={ABSTRACT,FALLBACK}
    assert again['report']['diagnostics']['cached_translations']>0


@pytest.mark.parametrize('original,changed,code',[
    ('The value x = 2 is retained.','値はy = 2として保持する。','inline_math_changed'),
    ('See https://example.invalid/paper for details.','詳細はhttps://example.invalid/other を参照する。','urls_changed'),
    ('MLLM obtains 91.012345 [1].','MLLMは91.012346を得る[1]。','numbers_changed'),
])
def test_text_recovery_does_not_relax_scientific_token_validation(original,changed,code):
    with pytest.raises(TranslationFailure) as e:validate_block(TranslatedBlock(block_id='a',translated_text=changed,status='translated'),original)
    assert e.value.code==code


@pytest.mark.parametrize('original,translated',[
    ('See https://example.invalid/paper for details.','https://example.invalid/paperの詳細を示す。'),
    ('The value is x = 2.','値はx = 2である。'),
])
def test_natural_japanese_punctuation_does_not_falsely_reject_unchanged_url_or_math(original,translated):
    validate_block(TranslatedBlock(block_id='a',translated_text=translated,status='translated'),original)


def test_oversized_ingest_candidate_cannot_protect_plain_abstract(client):
    original=make_reading_fixture();sid=upload(client,original)
    blocks=client.get(f'/api/sources/{sid}/document').json()['blocks']
    candidate=dict(blocks[0]);candidate.update(id='synthetic-stale-candidate',text='',kind='figure',role='figure_candidate',idx=999)
    candidate['bbox']={**candidate['bbox'],'rect':[44,38,551,735]};candidate['bbox_json']=json.dumps(candidate['bbox'])
    with fitz.open(stream=original,filetype='pdf') as doc:units=detect_units(doc,blocks+[candidate],[1])
    assert all(u['kind']=='text' for u in units if u.get('semantic_role')=='abstract_body')
    assert any(u.get('semantic_role')=='page_decoration' for u in units)


def test_real_drawing_over_abstract_is_protected_and_coverage_shows_incomplete(client):
    with fitz.open(stream=make_reading_fixture(),filetype='pdf') as d:
        d[0].draw_rect((45,213,540,298),color=(0,0,0),width=.4);original=d.tobytes()
    client.app.state.ai_runtime=ReadingRuntime();sid=upload(client,original)
    status,_=generate(client,sid,[1])
    # A real border over the paragraph remains fail-closed if its font/clip
    # cannot be proven; it must never be treated as successfully translated.
    assert status['state'] in ('preview_ready','failed')
    a=status['report']['coverage']['abstract'];assert a['protected_source_chars']>0 and not status['report']['coverage']['abstract_complete']
    assert a['translated_source_chars']==0 and a['candidate_source_chars']==0
    if status['state']=='failed':assert status['error']=='visual_font_validation_unavailable'
    else:assert all(q['status']=='passed' for q in status['report']['quality']['visuals'])


def test_old_whitespace_group_user_edit_is_not_overwritten(client):
    client.app.state.ai_runtime=ReadingRuntime();sid=upload(client,make_reading_fixture())
    v=client.get(f'/api/sources/{sid}/document').json()['version']['id']
    with closing(get_db()) as con:
        con.execute('''INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,user_edited,provider,created_at,updated_at)
            VALUES('old-spacing-edit',?,?,?,?,?,1,'user','old','old')''',(sid,v,'S'+'a'*63,ABSTRACT.replace(' ',''),'ユーザーが編集した旧本文訳'))
        con.commit();before=dict(con.execute("SELECT * FROM translations WHERE id='old-spacing-edit'").fetchone())
    status,_=generate(client,sid,[1]);assert status['state']=='preview_ready'
    abstract=next(u for u in status['report']['semantic_units'] if u['role']=='abstract_body')
    assert abstract['reason']=='assembled_user_edit_review'
    assert ABSTRACT not in [b['text'] for b in client.app.state.ai_runtime.inputs]
    with closing(get_db()) as con:assert dict(con.execute("SELECT * FROM translations WHERE id='old-spacing-edit'").fetchone())==before


@pytest.mark.parametrize('text',[
    'Nowadays, multimodal large language models remain readable.',
    'GPT-4o ViT-B/16 91.012345 https://example.invalid/a-b?q=2',
    'First sentence. Next sentence. Zero-shot and Few-shot remain unchanged.',
    'The value x = 2 is retained with accuracy 91.012345 [1].',
])
def test_narrow_word_spacing_preserves_identifiers_numbers_urls_and_sentences(text):
    with fitz.open() as doc:
        p=doc.new_page();p.insert_font(fontname='CMR',fontfile=str(cm_font()));words(p,text,100)
        data=doc.tobytes()
    extracted=extract_pdf(data)
    with fitz.open(stream=data,filetype='pdf') as doc:
        recovered=[]
        for b in extracted.blocks:
            if not b.text:continue
            value,safe=recover_text({'text':b.text,'bbox':b.bbox},glyph_lines(doc[0]))
            assert safe['glyph_text_matched'];recovered.append(value)
        assert re.sub(r'\s+',' ',' '.join(recovered))==text
        b=extracted.blocks[0]
        value,safe=recover_text({'text':b.text+'invented','bbox':b.bbox},glyph_lines(doc[0]))
        assert value is None and not safe['glyph_text_matched']


def test_unruled_two_column_table_with_one_numeric_value_per_row_is_protected(client):
    with fitz.open() as d:
        p=d.new_page()
        p.insert_text((45,50),'A normal research paragraph remains outside the table and is readable.',fontsize=11)
        for y,cells in [(150,['Model','Accuracy']),(170,['GPT-4o','91.012345']),(190,['ViT-B/16','92.987654'])]:
            for x,text in zip((45,220),cells):p.insert_text((x,y),text,fontsize=10)
        data=d.tobytes()
    sid=upload(client,data);blocks=client.get(f'/api/sources/{sid}/document').json()['blocks']
    with fitz.open(stream=data,filetype='pdf') as d:units=detect_units(d,blocks,[1])
    for value in ('GPT-4o','ViT-B/16','91.012345','92.987654'):
        assert all(u['kind']=='visual' for u in units if value in u['block']['text'])
    assert any(u['kind']=='text' for u in units if u['block']['text'].startswith('A normal'))


def test_short_final_line_does_not_become_a_one_line_following_page(tmp_path):
    from app.japanese_pdf import Reflow
    font=fitz.Font('cjk');(tmp_path/'kg-cjk.ttf').write_bytes(font.buffer)
    unit={'block':{'id':'original','text':'original','page':1},'kind':'text','status':'pending',
          'semantic_role':'paragraph','text':'文章の読み順と可読性を維持する。'*14}
    with fitz.open() as source,fitz.open() as output:
        source.new_page();flow=Reflow(source,output,[unit],fitz.Archive(str(tmp_path)),font)
        flow.y=720;flow.paragraph(unit)
        counts=[sum(len(b['lines']) for b in p.get_text('dict')['blocks'] if b['type']==0) for p in output]
        assert len(output)==2 and min(counts)>=2
        assert norm(''.join(p.get_text() for p in output))==norm(unit['text'])


def test_raised_footnote_mark_and_bottom_equation_are_distinct_from_folio(client):
    with fitz.open() as d:
        p=d.new_page(width=595,height=842)
        p.insert_text((45,50),'This ordinary paragraph remains research text and preserves its position.',fontsize=11)
        p.insert_text((45,754),'1',fontsize=7)
        p.insert_text((58,761),'Code and further information are available online.',fontsize=9)
        p.insert_text((150,790),'y = x + b (2)',fontsize=11)
        p.insert_text((292,820),'1',fontsize=10)
        data=d.tobytes()
    sid=upload(client,data);blocks=client.get(f'/api/sources/{sid}/document').json()['blocks']
    with fitz.open(stream=data,filetype='pdf') as d:units=detect_units(d,blocks,[1])
    foot=next(u for u in units if u.get('semantic_role')=='footnote')
    assert len(foot['original_block_ids'])==2
    assert len([u for u in units if u.get('semantic_role')=='footer'])==1
    equation=next(u for u in units if 'y = x' in u['block']['text']);assert equation['kind']=='visual'


@pytest.mark.parametrize('border',[False,True])
def test_short_abstract_keeps_its_role_and_never_disappears_from_quality_denominator(client,border):
    text='A short abstract explains the value x = 2'
    with fitz.open() as d:
        p=d.new_page();p.insert_text((50,50),'Abstract',fontsize=14)
        p.insert_text((50,85),text,fontsize=11)
        if border:p.draw_rect((45,68,400,94),width=.3)
        data=d.tobytes()
    sid=upload(client,data);blocks=client.get(f'/api/sources/{sid}/document').json()['blocks']
    with fitz.open(stream=data,filetype='pdf') as d:units=detect_units(d,blocks,[1])
    a=next(u for u in units if u.get('semantic_role')=='abstract_body')
    assert (a['kind']=='visual')==border
    result=coverage(units)['abstract']
    assert result['source_chars']==len(norm(text))
    assert result['protected_source_chars']==(len(norm(text)) if border else 0)


def test_cached_english_user_edit_is_protected_but_not_counted_as_japanese_abstract(client):
    client.app.state.ai_runtime=ReadingRuntime();sid=upload(client,make_reading_fixture())
    first,_=generate(client,sid,[1]);a=next(u for u in first['report']['semantic_units'] if u['role']=='abstract_body')
    with closing(get_db()) as con:
        con.execute('UPDATE translations SET translated_text=?,user_edited=1 WHERE id=?',(ABSTRACT,a['translation_id']));con.commit()
    status,_=generate(client,sid,[1],retry_failed=True)
    assert status['state']=='preview_ready' and status['report']['reading_quality']['abstract']=='incomplete'
    result=status['report']['coverage']['abstract'];assert result['translated_source_chars']==0
    assert result['placed_source_chars']==result['source_chars'] and result['placed_translated_source_chars']==0
    assert result['untranslated_reasons']=={'cached_not_japanese':len(norm(ABSTRACT))}
    with closing(get_db()) as con:
        row=con.execute('SELECT translated_text,user_edited FROM translations WHERE id=?',(a['translation_id'],)).fetchone()
        assert row['translated_text']==ABSTRACT and row['user_edited']
