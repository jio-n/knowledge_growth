"""Normal prose recovery, strict scientific crops, and independent stage states."""
from contextlib import closing
import json
import fitz
import pytest
from app.db import get_db
from app.japanese_content import detect_units
from app.japanese_overlap import InkProof,content_report
from app.japanese_pdf import norm
from app.japanese_visual_quality import cropped_visual,inspect_visual
from app.routes_japanese_pdf import snapshot
from tests.test_japanese_pdf import upload,generate
from tests.fixtures.japanese_overlap_factory import PARTS,JA,TITLE_JA
from tests.fixtures.japanese_recovery_factory import make_recovery_fixture,RecoveryRuntime


def detect(client,data):
    sid=upload(client,data)
    with closing(get_db()) as con:_,_,blocks,original,_=snapshot(con,sid)
    with fitz.open(stream=original,filetype='pdf') as doc:units=detect_units(doc,blocks,[1,2])
    return sid,units


def test_old_native_envelope_policy_loses_prose_new_policy_recovers_1150(client,monkeypatch):
    data=make_recovery_fixture()
    with monkeypatch.context() as old:
        old.setattr(InkProof,'prose_separated',lambda *args:False)
        _,before=detect(client,data)
    protected=sum(len(norm(u['block']['text'])) for u in before if u.get('semantic_role')=='abstract_body' and u['kind']=='visual')
    assert protected==1106
    sid,units=detect(client,data)
    report=content_report(units)[0]
    assert report['pair_counts']=={'overlap_unproven':5}
    assert len(report['prose_recovery'])==5 and not report['boundary_conflicts']
    assert all(p['glyph_membership_verified'] and not p['source_render_verified'] for p in report['pairs'])
    assert all(u['kind']=='text' for u in units if u.get('semantic_role')=='abstract_body')
    client.app.state.ai_runtime=RecoveryRuntime()
    status,response=generate(client,sid,[1,2]);assert status['state']=='preview_ready',status
    r=status['report'];a=r['coverage']['abstract']
    assert a['source_chars']==a['candidate_source_chars']==a['translated_source_chars']==a['placed_source_chars']==a['placed_translated_source_chars']==1150
    assert a['protected_source_chars']==0 and r['coverage']['abstract_complete']
    assert all(q['status']=='passed' and q['glyphs_preserved'] and q['fonts_preserved'] and q['drawings_preserved'] and q['images_preserved'] for q in r['quality']['visuals'])
    assert {q['asset_kind'] for q in r['quality']['visuals']}=={'figure','table','equation'}
    with fitz.open(stream=response.content,filetype='pdf') as doc:
        text=norm(''.join(p.get_text() for p in doc))
        positions=[text.index(norm(JA[p])) for p in PARTS]
        assert positions==sorted(positions) and all(text.count(norm(JA[p]))==1 for p in PARTS)
    assert all(u['font_size']==11 for u in r['units'] if u['semantic_role']=='abstract_body')
    assert client.get(f'/api/sources/{sid}/file').content==data
    assert all(p not in json.dumps(r) for p in PARTS)


def test_recovered_prose_uncertain_fallback_is_searchable_11pt_text(client):
    sid,_=detect(client,make_recovery_fixture());client.app.state.ai_runtime=RecoveryRuntime(fail_abstract=True)
    status,response=generate(client,sid,[1,2]);assert status['state']=='preview_ready',status
    a=status['report']['coverage']['abstract']
    assert a['source_chars']==a['candidate_source_chars']==a['placed_source_chars']==1150
    assert a['translated_source_chars']==a['protected_source_chars']==0
    assert a['untranslated_reasons']=={'ai_uncertain':1150}
    with fitz.open(stream=response.content,filetype='pdf') as doc:
        text=norm(''.join(p.get_text() for p in doc))
        assert all(text.count(norm(p))==1 for p in PARTS)
    assert not any(q['asset_kind']=='original_text' for q in status['report']['quality']['visuals'])


def test_native_span_membership_excludes_neighbouring_glyph_centres(client):
    sid,units=detect(client,make_recovery_fixture(tall_envelopes=True))
    abstract=[u for u in units if u.get('semantic_role')=='abstract_body']
    assert sum(len(norm(u['block']['text'])) for u in abstract)==1150
    assert all(u['kind']=='text' for u in abstract)
    assert all(b['_extraction']['native_span_membership'] for u in abstract for b in u['original_blocks'])
    client.app.state.ai_runtime=RecoveryRuntime()
    status,_=generate(client,sid,[1,2]);assert status['state']=='preview_ready',status
    assert status['report']['coverage']['abstract']['placed_translated_source_chars']==1150


@pytest.mark.parametrize('variant',['collision','graphic'])
def test_unsafe_prose_recovery_remains_closed(client,variant):
    _,units=detect(client,make_recovery_fixture(**{variant:True}))
    assert any(u['kind']=='visual' for u in units if u.get('semantic_role')=='abstract_body') or any(u.get('content_error') for u in units)


def test_clip_cannot_resurrect_original_prose(client):
    from app.japanese_semantics import classify_page
    sid,_=detect(client,make_recovery_fixture())
    with closing(get_db()) as con:_,_,blocks,data,_=snapshot(con,sid)
    with fitz.open(stream=data,filetype='pdf') as original,fitz.open(stream=make_recovery_fixture(clipped=True),filetype='pdf') as clipped:
        paragraphs=[b for b in classify_page(original[0],[b for b in blocks if b['page']==1]) if b['semantic_role']=='abstract_body']
        assert not InkProof(clipped[0]).prose_separated(*paragraphs[:2],[])


def test_boundary_redaction_loss_is_identified_without_text_disclosure():
    with fitz.open() as source,fitz.open() as output:
        p=source.new_page();p.insert_text((40,80),'Synthetic adjacent boundary text 91.012345',fontsize=11)
        # The default source extraction includes a character when sufficiently
        # inside, while outside text-only redaction removes its intersecting box.
        clip=fitz.Rect(40,69,160,83);diagnostics={}
        with cropped_visual(p,clip,diagnostics=diagnostics) as crop:
            q=output.new_page();target=fitz.Rect(44,44,44+clip.width,44+clip.height)
            q.show_pdf_page(target,crop,0)
            expected=p.get_text('text',clip=clip)
            asset={'id':'synthetic-crop','kind':'original_text','source_page':1,'source_bbox':list(clip),
                   'japanese_page':1,'japanese_bbox':{'rect':list(target)},'vector_preserved':True,
                   'crop_text_diagnostics':diagnostics,'original_block_chars':len(norm(p.get_text())),
                   'crop_no_hidden_text':norm(expected)==norm(crop[0].get_text('text',clip=fitz.INFINITE_RECT())),
                   'crop_text_complete':norm(expected)==norm(crop[0].get_text('text'))}
        report=inspect_visual(source,output,asset)
        assert report['status']=='failed' and report['error']=='visual_text_changed'
        assert diagnostics['boundary_straddling_glyphs']>0
        assert diagnostics['after_outside_redaction']['missing_chars']>0
        assert report['classification']=='text_missing_or_changed' and report['text_failure_stage']=='outside_text_redaction'
        assert not report['crop_text_complete'] and not report['source_text_preserved']
        assert not report['glyph_codes_preserved']
        safe=json.dumps(report);assert 'Synthetic' not in safe and '91.012345' not in safe


def test_hidden_outside_text_is_distinct_from_missing_crop_text():
    # Inject an untrimmed crop stream: it looks correct on the page but leaks
    # an off-page neighbour to extraction. Never accept raster identity alone.
    from app.japanese_visual_quality import text_counts
    with fitz.open() as source,fitz.open() as output:
        p=source.new_page();p.insert_text((40,80),'Synthetic selected paragraph',fontsize=11)
        p.insert_text((40,120),'Private neighbour excluded from crop',fontsize=11)
        clip=fitz.Rect(38,66,230,85);diagnostics={}
        with cropped_visual(p,clip,diagnostics=diagnostics) as crop:
            crop[0].insert_text((4,clip.height+30),'Private neighbour excluded from crop',fontsize=11)
            diagnostics['native_crop_all_text']=text_counts(p.get_text('text',clip=clip),crop[0].get_text('text',clip=fitz.INFINITE_RECT()))
            q=output.new_page();target=fitz.Rect(44,44,44+clip.width,44+clip.height)
            q.show_pdf_page(target,crop,0)
            expected=p.get_text('text',clip=clip)
            asset={'id':'hidden-neighbour','kind':'original_text','source_page':1,'source_bbox':list(clip),
                   'japanese_page':1,'japanese_bbox':{'rect':list(target)},'vector_preserved':True,
                   'crop_text_diagnostics':diagnostics,
                   'crop_no_hidden_text':norm(expected)==norm(crop[0].get_text('text',clip=fitz.INFINITE_RECT())),
                   'crop_text_complete':norm(expected)==norm(crop[0].get_text('text'))}
        result=inspect_visual(source,output,asset)
        assert result['source_text_preserved'] and result['crop_text_complete'] and not result['crop_no_hidden_text']
        assert result['error']=='visual_text_changed' and result['classification']=='unexpected_text'
        assert diagnostics['native_crop']['missing_chars']==diagnostics['native_crop']['extra_chars']==0
        assert diagnostics['native_crop_all_text']['extra_chars']>0
        assert 'Private neighbour' not in json.dumps(result)


def test_global_visual_failure_preserves_ai_success_and_precise_asset_state(client,monkeypatch):
    import app.japanese_pdf as engine
    quality=engine.visual_quality
    def damaged(source,output,asset):
        if asset['kind']=='table':
            r=fitz.Rect(asset['japanese_bbox']['rect'])
            output[asset['japanese_page']-1].draw_line((r.x0+2,r.y0+2),(r.x0+12,r.y0+2),width=.1)
        return quality(source,output,asset)
    runtime=RecoveryRuntime();client.app.state.ai_runtime=runtime
    sid,_=detect(client,make_recovery_fixture())
    with monkeypatch.context() as fault:
        fault.setattr(engine,'visual_quality',damaged)
        status,response=generate(client,sid,[1,2])
    assert status['state']=='failed' and response.status_code==404
    assert status['error']=='visual_content_changed'
    r=status['report'];title=next(u for u in r['semantic_units'] if u['role']=='paper_title')
    assert title['ai_translation_state']=='succeeded' and title['ai_translation_error'] is None
    assert title['placement_state']=='validated' and title['publication_state']=='failed'
    assert title['reason'] is None and title['visual_quality_state']=='not_applicable'
    assert r['coverage']['abstract']['placed_translated_source_chars']==1150
    assert r['diagnostics']['pdf_placement_succeeded']==0 and r['diagnostics']['publication_state']=='failed'
    failed=[u for u in r['units'] if u['visual_quality_state']=='failed']
    assert failed and all(u['asset_id']==next(q['asset_id'] for q in r['quality']['visuals'] if q['status']=='failed') for u in failed)
    with closing(get_db()) as con:
        saved=[dict(row) for row in con.execute('SELECT * FROM translations WHERE source_id=?',(sid,))]
    assert all(row['reason']!='visual_content_changed' for row in saved)
    assert any(norm(row['translated_text'])==norm(TITLE_JA) and row['translation_status']=='translated' for row in saved)
    # A subsequent failed publication must not even update the edited row's
    # status/reason/timestamp, not merely keep its translated string.
    with closing(get_db()) as con:
        con.execute('UPDATE translations SET user_edited=1 WHERE source_id=? AND translated_text=?',(sid,TITLE_JA));con.commit()
        edited=dict(con.execute('SELECT * FROM translations WHERE source_id=? AND user_edited=1',(sid,)).fetchone())
    with monkeypatch.context() as fault:
        fault.setattr(engine,'visual_quality',damaged)
        again,_=generate(client,sid,[1,2],retry_failed=True)
    assert again['state']=='failed'
    with closing(get_db()) as con:assert dict(con.execute('SELECT * FROM translations WHERE id=?',(edited['id'],)).fetchone())==edited
    calls=len(runtime.inputs)
    ready,_=generate(client,sid,[1,2],retry_failed=True);assert ready['state']=='preview_ready',ready
    assert ready['report']['diagnostics']['cached_translations']>=8
    assert all(b['text'] not in PARTS for b in runtime.inputs[calls:])


def test_local_crop_and_text_diagnostics_are_read_only_and_content_free(client,monkeypatch):
    import subprocess,sys
    monkeypatch.setattr('app.routes_sources._schedule_analysis',lambda *args:None)
    sid,_=detect(client,make_recovery_fixture())
    with closing(get_db()) as con:before=list(con.iterdump())
    result=subprocess.run([sys.executable,'scripts/diagnose_japanese_overlap.py','--source-id',sid,'--pages','1,2','--inspect-crops'],capture_output=True,text=True,check=True)
    report=json.loads(result.stdout)
    assert report['abstract']['candidate_source_chars']==1150 and report['abstract']['protected_source_chars']==0
    assert len(report['pages'][0]['prose_recovery'])==5 and len(report['crop_diagnostics'])==3
    assert all(c['crop_text_complete'] and c['crop_no_hidden_text'] and c['glyph_codes_preserved'] for c in report['crop_diagnostics'])
    assert not result.stderr and all(p not in result.stdout for p in PARTS)
    with closing(get_db()) as con:assert list(con.iterdump())==before
