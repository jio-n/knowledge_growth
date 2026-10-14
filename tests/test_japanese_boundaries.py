"""Padding-only separation, seed diagnostics and closed scientific boundaries."""
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
from app.japanese_overlap import InkProof,content_report
from app.japanese_pdf import norm
from app.routes_japanese_pdf import snapshot
from tests.fixtures.japanese_boundary_factory import make_boundary_fixture,BoundaryRuntime,PAIRS,pair_texts
from tests.fixtures.japanese_overlap_factory import PARTS,JA,TITLE,TITLE_JA
from tests.test_japanese_pdf import upload,generate


def inspect(client,sid):
    with closing(get_db()) as con:_,_,blocks,data,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as doc:units=detect_units(doc,blocks,[1,2,3])
    return units,content_report(units)[1]


def test_five_7782pt_font_envelopes_keep_seeds_and_complete_text_while_trimming_only_padding(client,caplog,capsys):
    runtime=BoundaryRuntime();client.app.state.ai_runtime=runtime
    data=make_boundary_fixture();sid=upload(client,data)
    before=client.get(f'/api/sources/{sid}/document').json()
    units,r=inspect(client,sid)
    assert r['pair_counts']=={'font_envelopes_only':5}
    assert [p['glyph_counts'] for p in r['pairs']]==[list(p) for p in PAIRS]
    assert all(p['minimum_baseline_difference_pt']==7.782 and p['source_render_verified'] and p['overlapping_ink_pixels']==0 for p in r['pairs'])
    assert len(r['boundary_checks'])==5 and not r['boundary_conflicts']
    assert all(c['before']['touches'] is True and c['minimum_crop']['touches'] is False and c['resolution']=='padding_trimmed' for c in r['boundary_checks'])
    assert all(g['crop_size_pt'][1]<g['before_crop_size_pt'][1] and g['mandatory_seed_and_font_boxes_preserved'] for g in r['regions'])
    assert all(s['kind']=='drawing' for s in r['seeds'])
    assert all(c['seed_ids'] and c['group_id'] and c['mandatory_seed_and_font_boxes_preserved'] for c in r['boundary_checks'])
    neighbors={pair['block_ids'][1] for pair in r['pairs']}
    assert all(u['kind']=='text' for u in units if u['block']['id'] in neighbors)
    status,response=generate(client,sid,[1,2,3]);assert status['state']=='preview_ready',status
    report=status['report'];a=report['coverage']['abstract']
    assert a['source_chars']==a['candidate_source_chars']==a['translated_source_chars']==a['placed_source_chars']==a['placed_translated_source_chars']==1150
    assert a['protected_source_chars']==0 and a['untranslated_reasons']=={}
    assert neighbors <= {u['block_id'] for u in report['units'] if u['kind']=='text' and u['status']=='failed' and u['reason']=='ai_uncertain'}
    assert all(u['font_size']==11 for u in report['units'] if u['block_id'] in neighbors)
    assert len(report['quality']['visuals'])==8 and report['quality']['failed_visuals']==0
    assert all(v['vector_preserved'] and v['drawings_preserved'] and v['images_preserved'] and v['glyphs_preserved'] and v['fonts_preserved'] for v in report['quality']['visuals'])
    assert {v['asset_kind'] for v in report['quality']['visuals'] if v['source_page']==3}=={'figure','table','equation'}
    assert report['quality']['missing_or_duplicate']==report['quality']['source_mapping_duplicates']==report['quality']['overlaps']==report['quality']['outside_page']==0
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=norm(''.join(p.get_text() for p in pdf));positions=[text.index(norm(JA[p])) for p in PARTS]
        assert positions==sorted(positions) and all(text.count(norm(JA[p]))==1 for p in PARTS)
        assert text.count(norm(TITLE_JA))==1 and '91.012345' in text
    assert client.get(f'/api/sources/{sid}/file').content==data
    after=client.get(f'/api/sources/{sid}/document').json()
    assert before['blocks']==after['blocks'] and before['version']==after['version']
    safe=json.dumps(report,ensure_ascii=False)+caplog.text+capsys.readouterr().out
    assert TITLE not in safe and TITLE_JA not in safe and all(p not in safe for p in PARTS)
    with TestClient(create_app(runtime=NoAIRuntime())) as restarted:
        assert restarted.get(f'/api/sources/{sid}/japanese-pdf/{status["generation_id"]}/file').content==response.content


def test_actual_ink_in_mandatory_crop_is_unresolved_and_not_published(client):
    runtime=BoundaryRuntime();client.app.state.ai_runtime=runtime
    sid=upload(client,make_boundary_fixture(inseparable=True));_,r=inspect(client,sid)
    assert r['boundary_conflicts']
    assert all(c['minimum_crop']['classification']=='ink_contact' and c['resolution']=='unresolved' for c in r['boundary_conflicts'])
    status,file=generate(client,sid,[1,2,3])
    assert status['state']=='failed' and status['error']=='visual_text_boundary_unresolved'
    assert status['report']['diagnostics']['failure_stage']=='content_detection' and not runtime.inputs and runtime.reviews==0
    assert file.status_code==404
    with closing(get_db()) as con:assert not con.execute('SELECT 1 FROM translations WHERE source_id=?',(sid,)).fetchone()


def unknown_neighbor(monkeypatch):
    original=InkProof.contact
    def contact(self,b,clip):
        r=original(self,b,clip)
        if r['classification']!='bbox_separated' and b['page']==2 and b['text']==pair_texts()[0][1] and clip.width>20 and fitz.Rect(b['bbox']['rect']).y0<150:
            r.update(touches=None,classification='contact_unproven',ink_pixels=None,proof_error='unsupported_glyph_rendering')
        return r
    monkeypatch.setattr(InkProof,'contact',contact)


def test_unproven_boundary_is_separate_from_real_ink_and_cannot_authorize_a_crop(client,monkeypatch):
    sid=upload(client,make_boundary_fixture());unknown_neighbor(monkeypatch)
    _,r=inspect(client,sid)
    assert len(r['boundary_conflicts'])==1
    failed=r['boundary_conflicts'][0]
    assert failed['before']['touches'] is None and failed['minimum_crop']['touches'] is None
    assert failed['minimum_crop']['classification']=='contact_unproven'
    assert failed['minimum_crop']['proof_error']=='unsupported_glyph_rendering'
    assert len([g for g in r['regions'] if g['crop_adjustment']=='padding_trimmed'])==4
    status,_=generate(client,sid,[1,2,3]);assert status['error']=='visual_text_boundary_unresolved'


def test_content_failure_preserves_all_preexisting_successful_and_edited_translations(client,monkeypatch):
    runtime=BoundaryRuntime();client.app.state.ai_runtime=runtime
    sid=upload(client,make_boundary_fixture());status,_=generate(client,sid,[1,2,3]);assert status['state']=='preview_ready'
    with closing(get_db()) as con:
        con.execute("UPDATE translations SET user_edited=1 WHERE source_id=? AND translated_text<>''",(sid,));con.commit()
        before=[dict(r) for r in con.execute('SELECT * FROM translations WHERE source_id=? ORDER BY id',(sid,))]
    runtime.inputs.clear();reviews=runtime.reviews;unknown_neighbor(monkeypatch)
    failed,_=generate(client,sid,[1,2,3],retry_failed=True)
    assert failed['state']=='failed' and not runtime.inputs and runtime.reviews==reviews
    with closing(get_db()) as con:assert [dict(r) for r in con.execute('SELECT * FROM translations WHERE source_id=? ORDER BY id',(sid,))]==before


def test_genuine_scientific_collision_remains_original_not_translated(client):
    sid=upload(client,make_boundary_fixture(collision=True));units,r=inspect(client,sid)
    unsafe=r['pairs'][0]
    assert unsafe['classification']=='overlap_unproven'
    assert all(u['kind']=='visual' for u in units if u['block']['id'] in unsafe['block_ids'])
    assert all(not u.get('translation_candidate') for u in units if u['block']['id'] in unsafe['block_ids'])


def test_local_diagnostic_identifies_seed_contact_and_padding_without_text_or_db_changes(client,monkeypatch):
    monkeypatch.setattr("app.routes_sources._schedule_analysis",lambda *args:None)
    sid=upload(client,make_boundary_fixture())
    with closing(get_db()) as con:before=list(con.iterdump())
    run=subprocess.run([sys.executable,'scripts/diagnose_japanese_overlap.py','--source-id',sid,'--pages','1,2,3'],capture_output=True,text=True,check=True)
    data=json.loads(run.stdout);r=data['pages'][1]
    assert len(r['boundary_checks'])==5 and len(r['seeds'])==5 and not r['boundary_conflicts']
    assert all(c['seed_ids'][0] in {s['id'] for s in r['seeds']} for c in r['boundary_checks'])
    assert data['abstract']['source_chars']==data['abstract']['candidate_source_chars']==1150
    assert not run.stderr and TITLE not in run.stdout and all(p not in run.stdout for p in PARTS)
    with closing(get_db()) as con:assert list(con.iterdump())==before


@pytest.mark.parametrize('state,code',[(True,'ink_contact'),(False,'bbox_only'),(None,'contact_unproven')])
def test_three_valued_contact_diagnosis_preserves_unknown_instead_of_asserting_ink(state,code):
    # Independently observable source evidence is exercised above. This guards
    # the public safe-report contract for all three outcomes.
    from app.japanese_boundaries import groups_for,seed
    class Proof:
        def contact(self,b,clip):return {'touches':state,'classification':code,'ink_pixels':None if state is None else int(state),'proof_error':'mask_unavailable' if state is None else None}
    bounds=fitz.Rect(0,0,595,842)
    blocks=[{'id':'b','text':'Untrusted ordinary content','bbox':{'rect':[40,40,140,50]},'role':None}]
    _,r=groups_for(Proof(),blocks,[seed('image',(40,40,50,50),bounds,0)],[],bounds,())
    assert r['seed_contacts'][0]['touches'] is state and r['seed_contacts'][0]['classification']==code
