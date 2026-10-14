"""Shared text/science classification, reading acceptance and one-unit retries."""
from contextlib import closing
import json
import re
import subprocess
import sys
from pathlib import Path
import fitz
import pytest
from fastapi.testclient import TestClient
from app.ai.offline import NoAIRuntime
from app.db import get_db
from app.main import create_app
from app.japanese_content import detect_units
from app.japanese_overlap import content_report
from app.japanese_pdf import norm
from app.japanese_textflow import equation_structure
from app.routes_japanese_pdf import snapshot
from tests.test_japanese_pdf import upload,generate
from tests.fixtures.japanese_lanes_factory import make_lanes_fixture,LanesRuntime,PARTS,TRANSLATIONS,TITLE_JA


def inspect(client,data):
    sid=upload(client,data)
    with closing(get_db()) as con:_,_,blocks,_,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as d:units=detect_units(d,blocks,[1,2])
    return sid,blocks,units


@pytest.mark.parametrize('frame,scale',[(False,1),(True,1),(False,.96)])
def test_dense_math_font_abstract_is_a_verified_text_stream_not_native_crops(client,frame,scale,caplog):
    runtime=LanesRuntime();client.app.state.ai_runtime=runtime
    data=make_lanes_fixture(frame=frame,horizontal_scale=scale)
    sid,blocks,units=inspect(client,data)
    abstract=[u for u in units if u.get('semantic_role')=='abstract_body']
    assert len(abstract)==7 and all(u['kind']=='text' for u in abstract)
    assert [norm(u['block']['text']) for u in abstract]==list(map(norm,PARTS))
    assert all(len(u['original_block_ids'])>1 for u in abstract[:-1])
    ids=[i for u in abstract for i in u['original_block_ids']];assert len(set(ids))==len(ids)
    source=next(r for r in content_report(units) if r['source_page']==1)
    assert source['classification_version']=='content-lanes-1' and not source['boundary_conflicts']
    assert all(s['kind']!='isolated_math_or_special_font' for s in source['seeds'])
    rows=[r for r in source['text_units'] if r['role']=='abstract_body']
    assert sum(r['source_chars'] for r in rows)==1150
    assert all(r['classification']=='text' and r['text_flow']['glyph_verified'] for r in rows)
    assert any(r['math_glyph_count']>r['glyph_count']*.5 for r in rows) if Path('/usr/share/fonts/truetype/lyx/cmmi10.ttf').exists() else True
    before=client.get(f'/api/sources/{sid}/document').json()
    result,pdf=generate(client,sid,[1,2]);assert result['state']=='preview_ready',result
    report=result['report'];a=report['coverage']['abstract']
    assert a['source_chars']==a['candidate_source_chars']==a['translated_source_chars']==a['placed_translated_source_chars']==1150
    assert a['protected_source_chars']==a['scientific_source_chars']==a['unresolved_text_source_chars']==0
    assert report['reading_quality']['accepted'] and report['reading_quality']['abstract']=='complete'
    assert {v['asset_kind'] for v in report['quality']['visuals']}=={'figure','table','equation'}
    assert all(v['status']=='passed' and v['vector_preserved'] and v['fonts_preserved'] and v['glyphs_preserved'] and v['drawings_preserved'] and v['images_preserved'] and v['mean_pixel_difference']==0 for v in report['quality']['visuals'])
    assert all(report['quality'][k]==0 for k in ('source_mapping_duplicates','missing_or_duplicate','overlaps','outside_page'))
    with fitz.open(stream=pdf.content,filetype='pdf') as d:
        text=norm(''.join(p.get_text() for p in d));positions=[text.index(norm(TRANSLATIONS[p])) for p in PARTS]
        assert positions==sorted(positions) and all(text.count(norm(TRANSLATIONS[p]))==1 for p in PARTS)
        assert text.count(norm(TITLE_JA))==1 and '□' not in text and '�' not in text
    assert all(u['font_size']==11 for u in report['units'] if u['semantic_role']=='abstract_body')
    assert client.get(f'/api/sources/{sid}/file').content==data
    after=client.get(f'/api/sources/{sid}/document').json();assert before['blocks']==after['blocks'] and before['version']==after['version']
    with TestClient(create_app(runtime=NoAIRuntime())) as restarted:assert restarted.get(f'/api/sources/{sid}/japanese-pdf/{result["generation_id"]}/file').content==pdf.content
    safe=json.dumps(report)+caplog.text;assert all(p not in safe for p in PARTS)


def test_partial_ai_failures_stay_english_text_and_only_requested_failure_is_retried(client):
    runtime=LanesRuntime(mixed=True);client.app.state.ai_runtime=runtime
    sid,_,_=inspect(client,make_lanes_fixture(frame=True))
    first,pdf=generate(client,sid,[1,2]);assert first['state']=='preview_ready',first
    r=first['report'];a=r['coverage']['abstract'];d=r['diagnostics']
    assert a['candidate_source_chars']==a['placed_source_chars']==1150 and a['protected_source_chars']==0
    assert d['translation_failures']=={'ai_uncertain':2,'japanese_required':1}
    assert r['reading_quality']['quality_validated'] and not r['reading_quality']['accepted']
    failed=[u for u in r['semantic_units'] if u['ai_translation_state']=='failed'];assert len(failed)==3
    for u in failed:
        assert u['content_lane']=='text' and u['placement_state']=='validated'
        assert u['visual_quality_state']=='not_applicable' and u['publication_state']=='published'
        check=u['translation_validation'][-1]
        assert check['japanese_chars']==0 and check['declared_status']==('uncertain' if u['ai_translation_error']=='ai_uncertain' else 'translated')
    with fitz.open(stream=pdf.content,filetype='pdf') as doc:
        text=norm(''.join(p.get_text() for p in doc))
        for part in (PARTS[2],PARTS[4],PARTS[5]):assert text.count(norm(part))==1
    with closing(get_db()) as con:
        con.execute('UPDATE translations SET user_edited=1 WHERE source_id=? AND translated_text=?',(sid,TITLE_JA));con.commit()
        previous={row['block_id']:dict(row) for row in con.execute('SELECT * FROM translations WHERE source_id=?',(sid,))}
    target=failed[0]['id'];runtime.failures.clear();start=len(runtime.inputs)
    retry,_=generate(client,sid,[1,2],retry_failed=True,retry_unit_ids=[target])
    assert retry['state']=='preview_ready',retry
    assert len(runtime.inputs[start:])==1 and runtime.inputs[start]['block_id']==target
    assert retry['report']['diagnostics']['retry_deferred']==2
    assert len([u for u in retry['report']['semantic_units'] if u['ai_translation_state']=='failed'])==2
    with closing(get_db()) as con:
        now={row['block_id']:dict(row) for row in con.execute('SELECT * FROM translations WHERE source_id=?',(sid,))}
    assert all(now[k]==row for k,row in previous.items() if k!=target)
    completed,_=generate(client,sid,[1,2],retry_failed=True)
    assert completed['report']['reading_quality']['accepted'] and completed['report']['coverage']['abstract']['ratio']==1


@pytest.mark.parametrize('selection',['unknown','successful','duplicate','other_pages'])
def test_unit_retry_rejects_foreign_or_successful_ids_before_ai(client,selection):
    runtime=LanesRuntime(mixed=True);client.app.state.ai_runtime=runtime
    sid,_,_=inspect(client,make_lanes_fixture());first,_=generate(client,sid,[1,2]);groups=first['report']['semantic_units']
    failed=next(u['id'] for u in groups if u['ai_translation_state']=='failed')
    successful=next(u['id'] for u in groups if u['ai_translation_state']=='succeeded')
    ids={'unknown':['not-a-source-id'],'successful':[successful],'duplicate':[failed,failed],'other_pages':[failed]}[selection]
    before=len(runtime.inputs)
    response=client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[1] if selection=='other_pages' else [1,2],'retry_failed':True,'retry_unit_ids':ids})
    assert response.status_code==422 and len(runtime.inputs)==before


@pytest.mark.parametrize('variant',['collision','unreadable'])
def test_actual_ink_collision_and_unsupported_text_are_not_scientific_by_font_alone(client,variant):
    sid,_,units=inspect(client,make_lanes_fixture(**{variant:True}))
    report=content_report(units)[0]
    protected=[r for r in report['text_units'] if r['role']=='abstract_body' and r['classification']!='text']
    assert protected
    if variant=='unreadable':
        assert any(r['text_flow']['glyph_error'] for r in protected)
        assert all(r['classification']=='unresolved_text' for r in protected)
        assert not any(s['kind']=='isolated_math_or_special_font' for s in report['seeds'])
    else:assert any(r['scientific_relations'] for r in protected)
    client.app.state.ai_runtime=LanesRuntime();result,_=generate(client,sid,[1,2])
    assert not result['report']['reading_quality']['accepted']


def test_number_corruption_is_rejected_independently_of_text_classification(client):
    runtime=LanesRuntime();runtime.failures={PARTS[1]:'numbers'};client.app.state.ai_runtime=runtime
    sid,_,_=inspect(client,make_lanes_fixture());result,_=generate(client,sid,[1,2]);r=result['report']
    assert result['state']=='preview_ready' and r['diagnostics']['translation_failures']=={'numbers_changed':1}
    assert r['coverage']['abstract']['protected_source_chars']==0
    assert not r['reading_quality']['accepted']
    assert len([u for u in r['semantic_units'] if u['ai_translation_state']=='succeeded'])>7


def test_local_abstract_classification_report_is_numeric_and_readonly(client,monkeypatch):
    monkeypatch.setattr('app.routes_sources._schedule_analysis',lambda *args:None)
    sid,_,_=inspect(client,make_lanes_fixture())
    with closing(get_db()) as con:before=list(con.iterdump())
    run=subprocess.run([sys.executable,'scripts/diagnose_japanese_overlap.py','--source-id',sid,'--pages','1,2','--inspect-crops'],capture_output=True,text=True,check=True)
    report=json.loads(run.stdout);rows=[u for u in report['pages'][0]['text_units'] if u['role']=='abstract_body']
    assert sum(u['source_chars'] for u in rows)==1150 and all(u['classification']=='text' for u in rows)
    assert all(u['text_flow']['evidence']=='native_glyph_correspondence' for u in rows)
    assert all(p not in run.stdout for p in PARTS) and not run.stderr
    with closing(get_db()) as con:assert list(con.iterdump())==before


@pytest.mark.parametrize('text,scientific',[
    ('MLLM models preserve source observations',False),('improves reproducibility [1200].',False),
    ('The value x = 2 is retained in ordinary prose.',False),('y = W x + b (1)',True),('∫ x dx = 2',True),
])
def test_mathematical_structure_requires_syntax_not_font_or_bbox(text,scientific):
    assert equation_structure(text)==scientific


def test_math_grid_without_equals_sign_preserves_scientific_spatial_structure(client):
    from tests.fixtures.japanese_reading_factory import cm_font
    math=cm_font().with_name('cmmi10.ttf')
    if not math.exists():pytest.skip('Optional Computer Modern fixture font unavailable')
    with fitz.open(stream=make_lanes_fixture(),filetype='pdf') as doc:
        p=doc[1];x=p.insert_font(fontname='Matrix',fontfile=str(math))
        typ,value=doc.xref_get_key(x,'DescendantFonts')
        if typ=='array':doc.xref_set_key(x,'BaseFont','/cmmi10');doc.xref_set_key(int(re.findall(r'\d+',value)[0]),'BaseFont','/cmmi10')
        for y,row in zip((690,712,734),('xyz','abc','def')):
            for x,c in zip((100,140,180),row):p.insert_text((x,y),c,fontname='Matrix',fontsize=11)
        data=doc.tobytes()
    runtime=LanesRuntime();client.app.state.ai_runtime=runtime
    sid,_,units=inspect(client,data)
    rows=[b for u in units for b in u.get('original_blocks',[]) if b.get('_text_flow',{}).get('evidence')=='mathematical_grid_geometry']
    assert len(rows)==9
    result,pdf=generate(client,sid,[1,2]);assert result['state']=='preview_ready',result
    equations=[a for a in result['report']['assets'] if a['kind']=='equation']
    assert len(equations)==2 and any(len(a['original_block_ids'])==9 for a in equations)
    assert all(v['status']=='passed' and v['mean_pixel_difference']==0 for v in result['report']['quality']['visuals'])
    assert all(b['text'] not in list('xyzabcdef') for b in runtime.inputs)


def test_edited_older_semantic_fragment_is_not_superseded_by_a_new_whole_paragraph(client):
    runtime=LanesRuntime();client.app.state.ai_runtime=runtime
    sid,_,units=inspect(client,make_lanes_fixture())
    first=next(u for u in units if u.get('semantic_role')=='abstract_body')
    fragment=' '.join(b['text'] for b in first['original_blocks'][:2])
    with closing(get_db()) as con:
        version=con.execute('SELECT id FROM source_versions WHERE source_id=?',(sid,)).fetchone()[0]
        con.execute('''INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,user_edited,provider,created_at,updated_at)
          VALUES('old-fragment',?,?,?,?,?,1,'user','old','old')''',(sid,version,'S'+'f'*63,fragment,'保存済みの部分的なユーザー編集'))
        con.commit();before=dict(con.execute("SELECT * FROM translations WHERE id='old-fragment'").fetchone())
    result,_=generate(client,sid,[1,2]);assert result['state']=='preview_ready',result
    assert not result['report']['reading_quality']['accepted']
    assert any(u['reason']=='assembled_user_edit_review' for u in result['report']['semantic_units'])
    assert not any(norm(b['text'])==norm(PARTS[0]) for b in runtime.inputs)
    with closing(get_db()) as con:assert dict(con.execute("SELECT * FROM translations WHERE id='old-fragment'").fetchone())==before


def test_sentences_inside_an_unlabelled_box_are_not_assumed_to_be_layout_decoration(client):
    # A prose-looking graph label without a section relationship is ambiguous.
    # Keep its original object instead of deleting a potentially scientific box.
    with fitz.open() as doc:
        p=doc.new_page();p.draw_rect((40,200,540,270),width=.3)
        p.insert_text((52,235),'This component describes a complete scientific processing stage.',fontsize=11)
        data=doc.tobytes()
    sid=upload(client,data)
    with closing(get_db()) as con:_,_,blocks,_,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as d:units=detect_units(d,blocks,[1])
    r=content_report(units)[0]
    assert r['seeds'] and not r['layout_decoration_objects']
    assert any(u['kind']=='visual' for u in units)
