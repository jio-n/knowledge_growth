"""Embedded-font regression, per-asset diagnosis and deliberate content damage."""
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import hashlib
import io
import json
import math
import re
import fitz
import pytest
from fastapi.testclient import TestClient
from app.ai.offline import NoAIRuntime
from app.config import generated_dir
from app.db import get_db
from app.main import create_app
from app.japanese_pdf import cropped_visual, visual_quality
from app.japanese_visual_quality import VisualQualityError, canonical_pixmap, pixel_mean, inspect_visual, PIXEL_LIMIT
from tests.test_japanese_pdf import upload,generate
from tests.fixtures.japanese_visual_factory import make_visual_fixture,VisualFaultRuntime,TITLE,UNCERTAIN


def copy_asset(kind='original_text',*,embedded=True,rotation=0,crop=False):
    source=fitz.open();p=source.new_page(width=595,height=842)
    font='helv'
    if embedded:
        font='Embedded';p.insert_font(fontname=font,fontbuffer=fitz.Font('cjk').buffer)
    p.insert_text((40.137,100.213),'Synthetic GPT-4o 91.012345 (1)',fontsize=23,fontname=font)
    if kind!='original_text':
        p.draw_rect((35,110,420,155),color=(0,0,0),width=.3)
        p.draw_line((50,125),(60,125),color=(0,0,0),width=.15)
        pix=fitz.Pixmap(fitz.csRGB,(0,0,12,12),False);pix.clear_with(80)
        p.insert_image((320,115,340,140),stream=pix.tobytes('png'))
    if crop:p.set_cropbox((20,20,575,822))
    p.set_rotation(rotation)
    # SourceAnchor geometry is unrotated, relative to CropBox.
    p.set_rotation(0)
    clip=fitz.Rect(30-(20 if crop else 0),65-(20 if crop else 0),440-(20 if crop else 0),160-(20 if crop else 0))
    output=fitz.open();q=output.new_page()
    target=fitz.Rect(44.273,90.719,44.273+clip.width,90.719+clip.height)
    with cropped_visual(p,clip) as visual:q.show_pdf_page(target,visual,0)
    p.set_rotation(rotation)
    asset={'id':'test-asset','kind':kind,'source_page':1,'source_bbox':list(clip),
           'japanese_page':1,'japanese_bbox':{'rect':list(target)},'vector_preserved':True}
    return source,output,asset


@pytest.mark.parametrize('kind',['original_text','figure','table','equation'])
def test_original_font_resource_copy_passes_for_every_kind(kind):
    source,output,asset=copy_asset(kind)
    with source,output:
        q=visual_quality(source,output,asset)
        assert q['source_text_preserved'] and q['glyphs_preserved'] and q['fonts_preserved']
        assert q['drawings_preserved'] and q['images_preserved'] and q['mean_pixel_difference']<=PIXEL_LIMIT
        assert q['quality_policy']==('original_text' if kind=='original_text' else 'scientific_visual')


def test_legacy_documentwriter_reencodes_embedded_font_and_reproduces_visual_content_changed():
    source,good,asset=copy_asset()
    with source,good,fitz.open() as bad:
        p=source[0];clip=fitz.Rect(asset['source_bbox'])
        stream=io.BytesIO();writer=fitz.DocumentWriter(stream);device=writer.begin_page(fitz.Rect(0,0,clip.width,clip.height))
        adapter=device if hasattr(device,'device') else SimpleNamespace(device=device.this)
        p.get_displaylist(annots=False).run(adapter,fitz.Matrix(1,0,0,1,-clip.x0,-clip.y0),fitz.Rect(0,0,clip.width,clip.height))
        writer.end_page();writer.close()
        with fitz.open(stream=stream.getvalue(),filetype='pdf') as converted:
            q=bad.new_page();q.show_pdf_page(fitz.Rect(asset['japanese_bbox']['rect']),converted,0)
        before=inspect_visual(source,bad,asset);after=visual_quality(source,good,asset)
        assert before['source_text_preserved'] and (before['raw_mean_pixel_difference'] is None or before['raw_mean_pixel_difference']>.2)
        assert before['status']=='failed' and before['asset_kind']=='original_text'
        assert after['mean_pixel_difference']==0 and after['vector_preserved']


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_cropbox_rotation_and_fractional_raster_phase_are_diagnostic_not_content_damage(rotation):
    source,output,asset=copy_asset('figure',embedded=False,rotation=rotation,crop=True)
    with source,output:
        quality=visual_quality(source,output,asset)
        assert quality['status']=='passed' and quality['source_rotation']==rotation
        assert quality['mean_pixel_difference']<=.2
        assert quality['classification']=='raster_alignment'
        assert quality['raw_mean_pixel_difference'] is None or quality['raw_mean_pixel_difference']>.2
        assert source[0].rotation==rotation


@pytest.mark.parametrize('kind',['original_text','figure','table','equation'])
def test_text_and_precision_damage_rejected_with_asset_kind(kind):
    source,output,asset=copy_asset(kind)
    with source,output:
        q=output[0];r=fitz.Rect(asset['japanese_bbox']['rect'])
        q.add_redact_annot(fitz.Rect(r.x0,r.y0,r.x1,r.y0+45),fill=False,cross_out=False)
        q.apply_redactions(images=0,graphics=0,text=0)
        with pytest.raises(VisualQualityError) as failure:visual_quality(source,output,asset)
        diag=failure.value.quality['visuals'][0]
        assert diag['asset_kind']==kind and not diag['source_text_preserved']
        assert diag['classification']=='text_missing_or_changed'
        assert 'GPT-4o' not in json.dumps(diag) and '91.012345' not in json.dumps(diag)


@pytest.mark.parametrize('damage',['line','image','cover','font'])
def test_scientific_content_damage_cannot_be_hidden_by_small_mean_pixel_difference(damage):
    source,output,asset=copy_asset('table')
    with source,output:
        q=output[0];target=fitz.Rect(asset['japanese_bbox']['rect']);clip=fitz.Rect(asset['source_bbox'])
        if damage=='line':
            # Remove one thin line's paint operator without touching text.
            removed=0
            for x in range(1,output.xref_length()):
                if output.xref_is_stream(x):
                    content=output.xref_stream(x)
                    if b'50 717 m' in content:
                        changed,count=re.subn(rb'50 717 m\s+60 717 l\s+\.15 w\s+0 0 0 RG S',b'',content)
                        removed+=count
                        output.update_stream(x,changed)
            assert removed>0
        elif damage=='image':
            xref=q.get_images()[0][0]
            pix=fitz.Pixmap(fitz.csRGB,(0,0,12,12),False);pix.clear_with(81)
            q.replace_image(xref,stream=pix.tobytes('png'))
        elif damage=='cover':
            q.draw_rect((target.x0+8,target.y0+8,target.x0+12,target.y0+12),fill=(1,1,1),color=None)
        else:
            # Same string with a different font is not enough to establish content.
            q.add_redact_annot((target.x0,target.y0,target.x1,target.y0+45),fill=False)
            q.apply_redactions(images=0,graphics=0,text=0)
            q.insert_text((target.x0+10.137,target.y0+35.213),'Synthetic GPT-4o 91.012345 (1)',fontsize=23,fontname='helv')
        diag=inspect_visual(source,output,asset)
        assert diag['status']=='failed'
        assert not all(diag[k] for k in ('glyphs_preserved','fonts_preserved','drawings_preserved','images_preserved'))
        if damage in ('image','line'):assert diag['mean_pixel_difference']<.2
        if damage=='image':assert not diag['images_preserved']
        if damage=='line':assert not diag['drawings_preserved']


def test_partial_eight_candidate_fixture_keeps_six_translations_and_two_safe_originals(client):
    runtime=VisualFaultRuntime();client.app.state.ai_runtime=runtime
    data=make_visual_fixture();sid=upload(client,data)
    status,response=generate(client,sid,[1]);d=status['report']['diagnostics']
    assert status['state']=='preview_ready',status
    assert d['translation_candidates']==8 and d['ai_translation_succeeded']==d['pdf_placement_succeeded']==6
    assert d['translation_failures']=={'ai_uncertain':1,'japanese_required':1}
    assert d['protected_units']==26
    visuals=status['report']['quality']['visuals']
    assert {'original_text','figure','table','equation'}=={a['asset_kind'] for a in visuals}
    assert len([a for a in visuals if a['asset_kind']=='original_text'])==2
    assert all(a['status']=='passed' and a['mean_pixel_difference']==0 for a in visuals)
    assert client.get(f'/api/sources/{sid}/file').content==data
    with fitz.open(stream=response.content,filetype='pdf') as pdf:
        text=''.join(p.get_text() for p in pdf)
        assert text.count(TITLE)==text.count(UNCERTAIN)==1
        assert '91.012345' in text and '92.987654' in text and 'y = W x + b (1)' in text
    runtime.all_translated=True
    recovered,_=generate(client,sid,[1],retry_failed=True)
    assert recovered['report']['diagnostics']['cached_translations']==6
    assert recovered['report']['diagnostics']['ai_translation_succeeded']==2
    assert len(runtime.inputs[-1])==2
    with TestClient(create_app(runtime=NoAIRuntime())) as reopened:
        assert reopened.get(f'/api/sources/{sid}/japanese-pdf').json()['state']=='preview_ready'


def test_every_failed_asset_is_reported_and_successful_ai_is_cached(client,monkeypatch,caplog,capsys):
    import app.japanese_pdf as engine
    visual=engine.Reflow.visual
    def corrupt(self,asset):
        visual(self,asset)
        if asset['kind'] in ('figure','table','equation'):
            rect=fitz.Rect(asset['japanese_bbox']['rect'])
            self.output[asset['japanese_page']-1].draw_line((rect.x0+2,rect.y0+2),(rect.x0+12,rect.y0+2),width=.1)
    monkeypatch.setattr(engine.Reflow,'visual',corrupt)
    client.app.state.ai_runtime=VisualFaultRuntime()
    data=make_visual_fixture();sid=upload(client,data)
    original_doc=client.get(f'/api/sources/{sid}/document').json()
    anchor={'blockId':original_doc['blocks'][0]['id'],'sourceVersion':original_doc['version']['id']}
    assert client.post('/api/highlights',json={'source_id':sid,'anchor':anchor,'comment':'Synthetic user comment'}).status_code==200
    assert client.post('/api/knowledge',json={'source_id':sid,'section_key':'insights','title':'Synthetic user note','content':'User content stays intact','origin':'user','info_type':'user_thought','anchor':anchor}).status_code==200
    with closing(get_db()) as con:
        user_before={table:[dict(r) for r in con.execute(f'SELECT * FROM {table} WHERE source_id=?',(sid,))]
                     for table in ('highlights','knowledge_items','paper_briefs')}
    status,response=generate(client,sid,[1])
    assert status['state']=='failed' and status['error']=='visual_content_changed'
    assert response.status_code==404 and not list(generated_dir().glob('*.pdf'))
    q=status['report']['quality'];failed=[a for a in q['visuals'] if a['status']=='failed']
    assert {'figure','table','equation'}=={a['asset_kind'] for a in failed}
    assert q['failed_visuals']==len(failed) and q['passed_visuals']==2
    assert status['report']['diagnostics']['ai_translation_succeeded']==6
    assert status['report']['diagnostics']['translation_failures']=={'ai_uncertain':1,'japanese_required':1}
    observed=json.dumps(q)+caplog.text+capsys.readouterr().out
    assert TITLE not in observed and '91.012345' not in observed
    assert client.get(f'/api/sources/{sid}/file').content==data
    with closing(get_db()) as con:
        assert con.execute("SELECT COUNT(*) FROM translations WHERE source_id=? AND translated_text<>''",(sid,)).fetchone()[0]==6
        assert user_before=={table:[dict(r) for r in con.execute(f'SELECT * FROM {table} WHERE source_id=?',(sid,))] for table in user_before}
    monkeypatch.setattr(engine.Reflow,'visual',visual)
    fresh,_=generate(client,sid,[1],retry_failed=True)
    assert fresh['state']=='preview_ready' and fresh['report']['diagnostics']['cached_translations']==6


def test_failed_crop_has_safe_asset_diagnostic(client,monkeypatch):
    import app.japanese_pdf as engine
    from app.japanese_translation import PDFError
    def fail(*args):raise PDFError('visual_crop_failed')
    monkeypatch.setattr(engine,'cropped_visual',fail)
    client.app.state.ai_runtime=VisualFaultRuntime()
    status,_=generate(client,upload(client,make_visual_fixture()),[1])
    assert status['error']=='visual_crop_failed'
    q=status['report']['quality']['visuals'][0]
    assert q['asset_kind']=='original_text' and q['classification']=='crop_unavailable'
    assert q['source_size_pt'][0]>0 and q['source_page']==1


def test_original_crop_excludes_hidden_neighbor_text_in_single_paint():
    with fitz.open() as doc:
        p=doc.new_page();p.insert_text((40,60),['Hidden neighbor before','Original fallback text','Hidden neighbor after'],fontsize=11,lineheight=2)
        with cropped_visual(p,fitz.Rect(38,68,200,88)) as crop:
            assert crop[0].get_text(clip=fitz.INFINITE_RECT()).strip()=='Original fallback text'


def test_annotation_render_is_verified_and_explicitly_nonvector(client):
    from tests.fixtures.japanese_pdf_factory import make_japanese_fixture
    with fitz.open(stream=make_japanese_fixture('reflow_demo'),filetype='pdf') as doc:
        p=doc[0];annot=p.add_rect_annot((100,100,200,130));annot.set_colors(stroke=(1,0,0));annot.update()
        data=doc.tobytes()
    status,_=generate(client,upload(client,data),[1,2,3])
    assert status['state']=='preview_ready',status
    q=next(q for q in status['report']['quality']['visuals'] if q['annotations_present'])
    assert not q['vector_preserved'] and q['annotation_raster_verified']
    assert q['source_text_preserved'] and q['mean_pixel_difference']<=.2


def test_freetext_annotation_keeps_appearance_without_hidden_annotation_text_duplication(client):
    from tests.fixtures.japanese_pdf_factory import make_japanese_fixture
    with fitz.open(stream=make_japanese_fixture('reflow_demo'),filetype='pdf') as doc:
        p=doc[0];a=p.add_freetext_annot((40,500,280,530),'Synthetic annotation 1.2345');a.update()
        data=doc.tobytes()
    status,_=generate(client,upload(client,data),[1,2,3])
    assert status['state']=='preview_ready',status
    q=next(q for q in status['report']['quality']['visuals'] if q['annotations_present'])
    assert q['annotation_raster_verified'] and q['crop_no_hidden_text'] and q['source_text_preserved']
    assert '_annotation_digest' not in json.dumps(status['report'])


def test_annotation_raster_tampering_is_detected_even_if_average_is_tiny(client,monkeypatch):
    import app.japanese_pdf as engine
    from tests.fixtures.japanese_pdf_factory import make_japanese_fixture
    visual=engine.Reflow.visual
    def corrupt(self,asset):
        visual(self,asset)
        if asset.get('rasterize_annotations'):
            p=self.output[asset['japanese_page']-1]
            xref=p.get_images()[-1][0]
            pix=fitz.Pixmap(self.output,xref)
            pix.set_pixel(1,1,(1,2,3))
            p.replace_image(xref,stream=pix.tobytes('png'))
    monkeypatch.setattr(engine.Reflow,'visual',corrupt)
    with fitz.open(stream=make_japanese_fixture('reflow_demo'),filetype='pdf') as doc:
        a=doc[0].add_rect_annot((40,500,200,530));a.update();data=doc.tobytes()
    status,_=generate(client,upload(client,data),[1,2,3])
    assert status['state']=='failed'
    q=next(q for q in status['report']['quality']['visuals'] if q['annotations_present'])
    assert q['classification']=='annotation_appearance_changed' and q['mean_pixel_difference']<.2


def test_font_program_name_alias_still_validates_native_resource_bytes():
    source,output,asset=copy_asset()
    with source,output:
        for doc in (source,output):
            for font in doc[0].get_fonts(full=True):doc.xref_set_key(font[0],'BaseFont','/SyntheticPostScriptAlias')
        q=visual_quality(source,output,asset)
        assert q['fonts_preserved'] and q['source_text_preserved']


def test_unverifiable_font_is_safe_failure_without_font_or_text_payload(monkeypatch):
    import app.japanese_visual_quality as quality
    from app.japanese_translation import PDFError
    source,output,asset=copy_asset()
    def unavailable(*args):raise PDFError('visual_font_validation_unavailable')
    monkeypatch.setattr(quality,'fonts',unavailable)
    with source,output:
        q=inspect_visual(source,output,asset)
        assert q['status']=='failed' and q['classification']=='validation_unavailable'
        assert q['source_page']==1 and q['asset_kind']=='original_text'
        assert 'Synthetic GPT' not in json.dumps(q)


def test_clip_size_change_is_not_accepted_as_raster_phase_difference():
    source,output,asset=copy_asset('figure')
    with source,output:
        asset['japanese_bbox']['rect'][2]+=.5
        q=inspect_visual(source,output,asset)
        assert q['status']=='failed' and q['classification']=='clip_geometry'
        assert not q['native_size_preserved']


def test_unwanted_text_in_original_fallback_is_rejected():
    source,output,asset=copy_asset()
    with source,output:
        r=fitz.Rect(asset['japanese_bbox']['rect'])
        output[0].insert_text((r.x0+10,r.y0+80),'Unwanted synthetic text',fontsize=9)
        q=inspect_visual(source,output,asset)
        assert q['status']=='failed' and q['classification']=='text_missing_or_changed'
        assert not q['source_text_preserved']


def test_common_origin_render_contains_real_ink_and_agrees_with_public_page_render():
    source,output,asset=copy_asset('table')
    with source,output:
        clip=fitz.Rect(asset['source_bbox']);scale=200/72
        canonical=canonical_pixmap(source[0],clip)
        page_render=source[0].get_pixmap(matrix=fitz.Matrix(scale,0,0,scale,-clip.x0*scale,-clip.y0*scale),clip=clip,colorspace=fitz.csRGB,alpha=False)
        assert any(value<255 for value in canonical.samples)
        assert pixel_mean(canonical,page_render)==0
        q=visual_quality(source,output,asset)
        assert q['source_ink_pixels']==q['output_ink_pixels']>0


def test_empty_destination_raster_is_not_accepted_when_source_has_real_content():
    source,output,asset=copy_asset()
    with source,output,fitz.open() as blank:
        blank.new_page()
        q=inspect_visual(source,blank,asset)
        assert q['status']=='failed' and q['source_ink_pixels']>0 and q['output_ink_pixels']==0
        assert q['mean_pixel_difference']>.2


def test_old_quality_artifact_is_preserved_but_not_returned_as_current_generation_cache(client):
    client.app.state.ai_runtime=VisualFaultRuntime()
    sid=upload(client,make_visual_fixture());old,original_file=generate(client,sid,[1])
    with closing(get_db()) as con:
        report=old['report'];report.pop('visual_quality_version')
        con.execute('UPDATE japanese_pdf_generations SET report_json=? WHERE id=?',(json.dumps(report),old['generation_id']));con.commit()
    fresh,_=generate(client,sid,[1])
    assert fresh['generation_id']!=old['generation_id'] and fresh['report']['visual_quality_version']=='native-2'
    assert fresh['report']['diagnostics']['cached_translations']==6
    assert client.get(f'/api/sources/{sid}/japanese-pdf/{old["generation_id"]}/file').content==original_file.content
