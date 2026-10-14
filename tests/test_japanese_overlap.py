"""Dense font envelopes must not remove readable Abstract prose from translation."""
from contextlib import closing
import json
import subprocess
import sys
import fitz
import pytest
from fastapi.testclient import TestClient
from app.ai.offline import NoAIRuntime
from app.db import get_db
from app.main import create_app
from app.japanese_content import detect_units
from app.japanese_overlap import content_report,InkProof
from app.japanese_pdf import norm
from app.routes_japanese_pdf import snapshot
from tests.test_japanese_pdf import upload,generate
from tests.fixtures.japanese_overlap_factory import make_overlap_fixture,OverlapRuntime,PARTS,JA,TITLE,TITLE_JA,INTRO


def job(client,**kwargs):
    sid=upload(client,make_overlap_fixture(**kwargs))
    with closing(get_db()) as con:source,version,blocks,data,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as doc:units=detect_units(doc,blocks,[1,2])
    return sid,source,version,blocks,units


def test_dense_1150_character_abstract_uses_verified_ink_not_font_boxes(client,caplog,capsys):
    runtime=OverlapRuntime();client.app.state.ai_runtime=runtime
    sid,_,_,blocks,units=job(client)
    abstract=[u for u in units if u.get('semantic_role')=='abstract_body']
    assert [len(norm(u['block']['text'])) for u in abstract]==[186,184,183,183,184,186,44]
    assert all(u['kind']=='text' and u['status']=='pending' for u in abstract)
    pairs=content_report(units)[0]['pairs'];assert len(pairs)==5
    assert all(p['classification']=='font_envelopes_only' and p['source_render_verified'] and p['overlapping_ink_pixels']==0 for p in pairs)
    # These generated source rectangles meet the removed old rule, while actual
    # source glyph programs, origins and ink prove that adjacent lines are clear.
    original={b['id']:b for b in blocks}
    for pair in pairs:
        a,b=[fitz.Rect(original[bid]['bbox']['rect']) for bid in pair['block_ids']]
        assert (a & b).height>min(a.height,b.height)*.18
        assert pair['minimum_baseline_difference_pt']>=13
    before=client.get(f'/api/sources/{sid}/document').json()
    source_pdf=client.get(f'/api/sources/{sid}/file').content
    result,response=generate(client,sid,[1,2]);assert result['state']=='preview_ready',result
    r=result['report'];a=r['coverage']['abstract']
    assert a['source_chars']==a['candidate_source_chars']==a['translated_source_chars']==a['placed_source_chars']==a['placed_translated_source_chars']==1150
    assert a['protected_source_chars']==0 and a['untranslated_reasons']=={} and r['coverage']['abstract_complete']
    assert r['visual_quality_version']=='native-3' and {a['kind'] for a in r['assets']}=={'figure','table','equation'}
    assert all(v['status']=='passed' and v['drawings_preserved'] and v['images_preserved'] and v['vector_preserved'] for v in r['quality']['visuals'])
    assert r['quality']['source_mapping_duplicates']==r['quality']['missing_or_duplicate']==r['quality']['outside_page']==r['quality']['overlaps']==0
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=norm(''.join(p.get_text() for p in pdf))
        positions=[text.index(norm(JA[p])) for p in PARTS]
        assert positions==sorted(positions) and all(text.count(norm(JA[p]))==1 for p in PARTS)
        assert norm(INTRO) in text and text.count(norm(TITLE_JA))==1
        assert '�' not in text and '□' not in text
        assert all(u['font_size']==11 for u in r['units'] if u['kind']=='text' and u['semantic_role']=='abstract_body')
    after=client.get(f'/api/sources/{sid}/document').json()
    assert before['blocks']==after['blocks'] and before['version']==after['version']
    assert client.get(f'/api/sources/{sid}/file').content==source_pdf
    with TestClient(create_app(runtime=NoAIRuntime())) as restarted:
        assert restarted.get(f'/api/sources/{sid}/japanese-pdf/{result["generation_id"]}/file').content==response.content
    safe=json.dumps(r,ensure_ascii=False)+caplog.text+capsys.readouterr().out
    assert TITLE not in safe and TITLE_JA not in safe and all(p not in safe for p in PARTS)


def test_uncertain_abstract_is_readable_english_text_with_accurate_coverage(client):
    client.app.state.ai_runtime=OverlapRuntime(fail_abstract=True)
    sid=upload(client,make_overlap_fixture());status,response=generate(client,sid,[1])
    assert status['state']=='preview_ready',status
    a=status['report']['coverage']['abstract']
    assert a['source_chars']==a['candidate_source_chars']==a['placed_source_chars']==1150
    assert a['translated_source_chars']==a['protected_source_chars']==a['placed_translated_source_chars']==0
    assert a['untranslated_reasons']=={'ai_uncertain':1150}
    assert not any(u['kind']=='visual' for u in status['report']['units'] if u['semantic_role']=='abstract_body')
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=norm(''.join(p.get_text() for p in pdf));assert all(text.count(norm(p))==1 for p in PARTS)
    assert all(u['font_size']==11 for u in status['report']['units'] if u.get('semantic_role')=='abstract_body')


@pytest.mark.parametrize('variant',['collision','graphic'])
def test_real_collisions_or_graphics_cannot_become_safe_translatable_prose(client,variant):
    sid,_,_,_,units=job(client,**{variant:True})
    abstract=[u for u in units if u.get('semantic_role')=='abstract_body']
    assert any(u['kind']=='visual' for u in abstract) or any(u.get('content_error') for u in units)
    pairs=content_report(units)[0]['pairs']
    if variant=='collision':assert any(p['classification']!='font_envelopes_only' for p in pairs)
    # Unrelated final prose must not be recursively engulfed by a collision.
    final=next(u for u in units if u['block']['text']==PARTS[6])
    assert final['kind']=='text'
    assert all(region['expansion_steps']==1 for page in content_report(units) for region in page['regions'])


def test_unavailable_glyph_proof_and_comparison_limit_fail_closed(client,monkeypatch):
    original_mask=InkProof.mask
    monkeypatch.setattr(InkProof,'mask',lambda *args:None)
    _,_,_,_,units=job(client)
    assert all(p['classification']=='overlap_unproven' for p in content_report(units)[0]['pairs'])
    assert any(u['kind']=='visual' for u in units if u.get('semantic_role')=='abstract_body')
    monkeypatch.setattr(InkProof,'mask',original_mask)
    monkeypatch.setattr('app.japanese_overlap.MAX_OVERLAP_PAIRS',1)
    sid,_,_,_,units=job(client)
    assert any(u.get('content_error')=='overlap_proof_limit' for u in units)
    status,_=generate(client,sid,[1]);assert status['state']=='failed' and status['error']=='overlap_proof_limit'
    assert status['report']['diagnostics']['failure_stage']=='content_detection'
    with closing(get_db()) as con:assert con.execute('SELECT count(*) FROM translations WHERE source_id=?',(sid,)).fetchone()[0]==0


def test_local_numeric_diagnostic_is_read_only_and_never_emits_content(client,monkeypatch):
    monkeypatch.setattr("app.routes_sources._schedule_analysis",lambda *args:None)
    from app.config import db_path
    sid=upload(client,make_overlap_fixture())
    with closing(get_db()) as con:original=list(con.iterdump())
    command=[sys.executable,'scripts/diagnose_japanese_overlap.py','--source-id',sid,'--pages','1,2']
    run=subprocess.run(command,capture_output=True,text=True,check=True)
    report=json.loads(run.stdout);assert not run.stderr and report['overlap_version']=='glyph-ink-2'
    assert report['abstract']['candidate_source_chars']==1150 and report['abstract']['protected_source_chars']==0
    assert all(p not in run.stdout for p in PARTS) and TITLE not in run.stdout and 'fontbuffer' not in run.stdout
    with closing(get_db()) as con:assert list(con.iterdump())==original
    bad=subprocess.run(command[:-1]+['999'],capture_output=True,text=True)
    assert bad.returncode==1 and json.loads(bad.stdout)=={'error':'local_diagnostic_unavailable'} and not bad.stderr
