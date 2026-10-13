"""Invisible Form commands must not be confused with visible content damage."""
import hashlib
import json
import fitz
import pytest
from app.japanese_visual_quality import inspect_visual, visual_quality, PIXEL_LIMIT
from tests.fixtures.japanese_visual_factory import make_tiny_original_crop, add_hidden_form
from tests.fixtures.japanese_visual_factory import make_visual_fixture, VisualFaultRuntime
from tests.test_japanese_pdf import upload, generate
from tests.test_japanese_visual_quality import copy_asset


def test_tiny_original_text_foreign_crop_is_exact_and_only_hidden_commands_differ():
    source,output,asset=make_tiny_original_crop()
    with source,output:
        source[0].set_rotation(0)
        before=hashlib.sha256(source.tobytes(no_new_id=True)).digest()
        q=visual_quality(source,output,asset)
        assert q['source_size_pt']==[395.157,17.215]
        assert q['source_image_size_px']==q['output_image_size_px']==[1098,48]
        assert q['mean_pixel_difference']==q['raw_mean_pixel_difference']==0
        assert q['source_text_preserved'] and q['glyphs_preserved'] and q['fonts_preserved']
        assert not q['drawings_preserved'] and q['images_preserved']
        assert q['source_drawing_count']==2 and q['output_drawing_count']==4
        assert q['output_invisible_drawing_count']==2
        assert q['visible_drawings_preserved'] and q['nonvisible_drawing_difference_proven']
        assert q['classification']=='nonvisible_drawing_commands' and q['vector_preserved']
        assert hashlib.sha256(source.tobytes(no_new_id=True)).digest()==before
        report=json.dumps(q)
        assert 'Synthetic' not in report and 'digest' not in report and 'Embedded' not in report


@pytest.mark.parametrize('kind',['figure','table','equation'])
def test_scientific_visual_does_not_waive_structure_mismatch_even_for_identical_pixels(kind):
    source,output,asset=make_tiny_original_crop();asset['kind']=kind
    with source,output:
        q=inspect_visual(source,output,asset)
        assert q['mean_pixel_difference']==0 and q['source_text_preserved']
        assert q['status']=='failed' and q['classification']=='drawing_changed'
        assert not q['drawings_preserved'] and q['images_preserved']
        assert q['quality_policy']=='scientific_visual'


@pytest.mark.parametrize('damage',['text','glyph','font','line','image'])
def test_original_text_visible_damage_is_not_waived(damage):
    source,output,asset=copy_asset('table');asset['kind']='original_text'
    with source,output:
        if damage!='image':add_hidden_form(output,asset)
        q=output[0];target=fitz.Rect(asset['japanese_bbox']['rect'])
        if damage in ('text','glyph','font'):
            q.add_redact_annot((target.x0,target.y0,target.x1,target.y0+45),fill=False)
            q.apply_redactions(images=0,graphics=0,text=0)
            if damage!='text':
                # Reinsert the same string but change either geometry or font.
                name='Embedded' if damage=='glyph' else 'helv'
                if damage=='glyph':q.insert_font(fontname=name,fontbuffer=fitz.Font('cjk').buffer)
                q.insert_text((target.x0+10.137+(1 if damage=='glyph' else 0),target.y0+35.213),
                              'Synthetic GPT-4o 91.012345 (1)',fontsize=23,fontname=name)
        elif damage=='line':
            q.draw_line((target.x0+3,target.y0+65),(target.x0+10,target.y0+65),width=.1)
        else:
            pix=fitz.Pixmap(fitz.csRGB,(0,0,12,12),False);pix.clear_with(81)
            q.replace_image(q.get_images()[0][0],stream=pix.tobytes('png'))
        result=inspect_visual(source,output,asset)
        assert result['status']=='failed'
        if damage in ('line','image'):
            assert result['mean_pixel_difference']<PIXEL_LIMIT
            assert result['classification']==('drawing_changed' if damage=='line' else 'image_changed')
        if damage=='glyph':assert result['classification']=='glyph_geometry_changed'
        if damage=='font':assert result['classification']=='font_resource_changed'


def test_exact_render_is_required_for_nonvisible_drawing_exception():
    source,output,asset=copy_asset()
    with source,output:
        add_hidden_form(output,asset)
        # A nearly invisible added image still changes native image resources.
        pix=fitz.Pixmap(fitz.csRGB,(0,0,1,1),False);pix.clear_with(254)
        r=fitz.Rect(asset['japanese_bbox']['rect'])
        output[0].insert_image((r.x0+2,r.y0+2,r.x0+3,r.y0+3),stream=pix.tobytes('png'))
        q=inspect_visual(source,output,asset)
        assert 0<q['mean_pixel_difference']<PIXEL_LIMIT
        assert q['status']=='failed' and not q['nonvisible_drawing_difference_proven']


def test_visible_nonrectangular_clip_cannot_be_proven_safe_from_its_scissor():
    source,output,asset=copy_asset()
    with source,output,fitz.open() as foreign:
        p=foreign.new_page(width=600,height=800)
        # A triangular clip hides a line, although its bounding scissor overlaps
        # the target. A bounding rectangle is not evidence of visible content.
        x=foreign.get_new_xref();foreign.update_object(x,'<<>>')
        foreign.update_stream(x,b'q 0 700 m 500 700 l 500 800 l h W n 0 770 m 100 770 l .1 w 0 G S Q')
        p.set_contents(x)
        r=fitz.Rect(asset['japanese_bbox']['rect'])
        output[0].show_pdf_page(fitz.Rect(r.x0,r.y0,r.x0+500,r.y0+100),foreign,0,clip=fitz.Rect(0,0,500,100))
        q=inspect_visual(source,output,asset)
        assert q['status']=='failed' and q['classification']=='drawing_changed'
        assert not q['visible_drawings_verified']


def test_clip_boundary_line_is_kept_and_visible_extra_stroke_is_rejected():
    source,output,asset=make_tiny_original_crop()
    with source,output:
        assert visual_quality(source,output,asset)['visible_drawings_preserved']
        r=fitz.Rect(asset['japanese_bbox']['rect'])
        # The centreline lies outside; the stroke crosses the crop boundary.
        output[1].draw_line((r.x0+5,r.y1+.05),(r.x0+15,r.y1+.05),width=.2)
        q=inspect_visual(source,output,asset)
        assert q['status']=='failed' and not q['visible_drawings_preserved']
        assert q['mean_pixel_difference']<PIXEL_LIMIT


def test_failed_old_structure_check_recovers_one_original_asset_and_reuses_six_translations(client,monkeypatch):
    import app.japanese_pdf as engine
    from app.japanese_visual_quality import VisualQualityError
    native=engine.Reflow.visual;quality=engine.visual_quality
    def reflow_with_foreign_form(self,asset):
        native(self,asset)
        if asset['kind']=='original_text' and sum(a['kind']=='original_text' for a in self.assets)==1:
            add_hidden_form(self.output,asset)
    def previous_policy(source,output,asset):
        q=inspect_visual(source,output,asset)
        if not q['drawings_preserved']:
            q.update(status='failed',error='visual_content_changed',classification='drawing_or_image_changed')
            raise VisualQualityError([q])
        return q
    monkeypatch.setattr(engine.Reflow,'visual',reflow_with_foreign_form)
    monkeypatch.setattr(engine,'visual_quality',previous_policy)
    runtime=VisualFaultRuntime();client.app.state.ai_runtime=runtime
    data=make_visual_fixture();sid=upload(client,data)
    failed,_=generate(client,sid,[1])
    assert failed['state']=='failed' and failed['error']=='visual_content_changed'
    assert failed['report']['quality']['failed_visuals']==1
    diag=next(q for q in failed['report']['quality']['visuals'] if q['status']=='failed')
    assert diag['asset_kind']=='original_text' and diag['mean_pixel_difference']==0
    assert diag['source_text_preserved'] and diag['images_preserved']
    monkeypatch.setattr(engine,'visual_quality',quality)
    calls=len(runtime.inputs)
    recovered,pdf=generate(client,sid,[1],retry_failed=True)
    assert recovered['state']=='preview_ready' and pdf.status_code==200
    d=recovered['report']['diagnostics']
    assert d['cached_translations']==d['pdf_placement_succeeded']==6
    assert d['translation_failures']=={'ai_uncertain':1,'japanese_required':1}
    retried={b['text'] for batch in runtime.inputs[calls:] for b in batch}
    from tests.fixtures.japanese_visual_factory import TITLE,UNCERTAIN
    assert retried=={TITLE,UNCERTAIN}
    assert recovered['report']['quality']['failed_visuals']==0
    assert any(q['nonvisible_drawing_difference_proven'] for q in recovered['report']['quality']['visuals'] if q['asset_kind']=='original_text')
    assert client.get(f'/api/sources/{sid}/file').content==data


@pytest.mark.parametrize('kind',['original_text','figure','table','equation'])
def test_visible_line_clipping_damage_rejected_even_when_paint_commands_match(kind):
    from app.japanese_visual_quality import cropped_visual
    source,old,asset=copy_asset();asset['kind']=kind
    with source,old,fitz.open() as output:
        clip=fitz.Rect(asset['source_bbox']);target=fitz.Rect(asset['japanese_bbox']['rect'])
        source[0].draw_line((50,159),(60,159),width=.1)
        shortened=fitz.Rect(clip.x0,clip.y0,clip.x1,clip.y1-2)
        q=output.new_page()
        with cropped_visual(source[0],shortened) as native:
            q.show_pdf_page(fitz.Rect(target.x0,target.y0,target.x1,target.y1-2),native,0)
        result=inspect_visual(source,output,asset)
        assert result['status']=='failed' and result['classification']=='drawing_changed'
        assert result['drawings_preserved'] and result['source_text_preserved']
        assert not result['visible_drawings_preserved']
        assert 0<result['mean_pixel_difference']<PIXEL_LIMIT
