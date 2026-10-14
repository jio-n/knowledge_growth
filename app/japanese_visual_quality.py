"""Native-resource visual crops and content-aware, phase-normalized quality checks.

Only the numeric/boolean result is public. Glyphs, fonts, paths and image bytes
are compared in memory; no source text, image, hash or vendor payload is reported.
"""
import hashlib
import math
import re
from collections import Counter
import fitz
from PIL import Image, ImageChops
from .japanese_translation import PDFError

DPI=200
MAX_VISUAL_PIXELS=16_000_000
VISUAL_QUALITY_VERSION='native-3'
PIXEL_LIMIT=.2  # unchanged; never waived by a matching text string
KINDS=frozenset({'original_text','figure','table','equation'})


def norm(text): return re.sub(r'\s+','',text)


class VisualQualityError(PDFError):
    def __init__(self, visuals):
        failed=[v for v in visuals if v['status']=='failed']
        super().__init__(failed[0]['error'] if failed else 'visual_validation_failed')
        self.quality={'visual_quality_version':VISUAL_QUALITY_VERSION,'visuals':visuals,
                      'failed_visuals':len(failed),'passed_visuals':len(visuals)-len(failed)}


def text_counts(expected,actual):
    """Counts only. No source strings, glyph codes or text hashes escape."""
    a,b=Counter(norm(expected)),Counter(norm(actual))
    return {'expected_chars':sum(a.values()),'actual_chars':sum(b.values()),
            'missing_chars':sum((a-b).values()),'extra_chars':sum((b-a).values()),
            'order_matches':norm(expected)==norm(actual)}


def cropped_visual(source,clip,*,diagnostics=None):
    """Copy original font/image/vector resources, suppress hidden outside text.

    DocumentWriter re-encodes embedded fonts and can change raster hinting/phase.
    Work only on a disposable native copy. No fill, image or graphics redaction.
    A Form clip alone is insufficient: PDF.js extracts hidden outside text.
    """
    copy=fitz.open()
    cropped=fitz.open()
    try:
        copy.insert_pdf(source.parent,from_page=source.number,to_page=source.number,annots=False)
        page=copy[0]; page.set_rotation(0)
        expected=page.get_text('text',clip=clip)
        if diagnostics is not None:
            diagnostics['source_clip_chars']=len(norm(expected))
            diagnostics['boundary_straddling_glyphs']=sum(
                fitz.Rect(bbox).intersects(clip) and not clip.contains(fitz.Rect(bbox))
                for trace in page.get_texttrace() for _,_,_,bbox in trace['chars'])
        spans=[s for b in page.get_text('dict',clip=fitz.INFINITE_RECT())['blocks'] if b['type']==0
               for line in b['lines'] for s in line['spans']]
        if any(not clip.contains(fitz.Rect(s['bbox'])) for s in spans):
            outer=fitz.Rect(page.rect)
            for s in spans: outer |= fitz.Rect(s['bbox'])
            outer=fitz.Rect(outer.x0-3,outer.y0-3,outer.x1+3,outer.y1+3)
            outside=[fitz.Rect(outer.x0,outer.y0,clip.x0,outer.y1),fitz.Rect(clip.x1,outer.y0,outer.x1,outer.y1),
                     fitz.Rect(outer.x0,outer.y0,outer.x1,clip.y0),fitz.Rect(outer.x0,clip.y1,outer.x1,outer.y1)]
            for area in outside:
                if not area.is_empty: page.add_redact_annot(area,fill=False,cross_out=False)
            page.apply_redactions(images=0,graphics=0,text=0)
        if diagnostics is not None:
            diagnostics['after_outside_redaction']=text_counts(expected,page.get_text('text',clip=clip))
        target=cropped.new_page(width=clip.width,height=clip.height)
        target.show_pdf_page(target.rect,copy,0,clip=clip)
        if diagnostics is not None:
            diagnostics['native_crop']=text_counts(expected,target.get_text('text'))
            diagnostics['native_crop_all_text']=text_counts(expected,target.get_text('text',clip=fitz.INFINITE_RECT()))
        return cropped
    except Exception:
        cropped.close()
        raise PDFError('visual_crop_failed') from None
    finally:
        copy.close()


def canonical_pixmap(page,clip):
    """Render a shared local pixel origin, independent of page/CropBox phase."""
    scale=DPI/72
    bounds=fitz.IRect(0,0,math.ceil(clip.width*scale),math.ceil(clip.height*scale))
    if bounds.width*bounds.height>MAX_VISUAL_PIXELS: raise PDFError('visual_too_large_for_validation')
    rendered=page.get_displaylist(annots=True).get_pixmap(
        matrix=fitz.Matrix(scale,0,0,scale,-clip.x0*scale,-clip.y0*scale),
        clip=clip,colorspace=fitz.csRGB,alpha=0)
    # A float boundary may round the display-list raster to -1 or add one edge
    # pixel. Copy by its local pixel coordinates into the same fixed test frame.
    pix=fitz.Pixmap(fitz.csRGB,bounds,False);pix.clear_with(255);pix.copy(rendered,bounds)
    return pix


def pixel_mean(a,b):
    if (a.width,a.height)!=(b.width,b.height): return None
    ia=Image.frombytes('RGB',(a.width,a.height),a.samples)
    ib=Image.frombytes('RGB',(b.width,b.height),b.samples)
    hist=ImageChops.difference(ia,ib).histogram()
    return sum((i%256)*count for i,count in enumerate(hist))/(a.width*a.height*3)


def ink_pixels(pix):
    return sum(Image.frombytes('RGB',(pix.width,pix.height),pix.samples).convert('L').histogram()[:255])


def relative(value,clip):
    if isinstance(value,fitz.Point): return (value.x-clip.x0,value.y-clip.y0)
    if isinstance(value,fitz.Rect): return (value.x0-clip.x0,value.y0-clip.y0,value.x1-clip.x0,value.y1-clip.y0)
    if isinstance(value,fitz.Quad): return tuple(relative(p,clip) for p in value)
    if isinstance(value,(list,tuple)): return tuple(relative(v,clip) for v in value)
    return value


def close_values(a,b):
    if type(a) in (int,float) and type(b) in (int,float): return abs(a-b)<=.02
    if isinstance(a,(tuple,list)) and isinstance(b,(tuple,list)):
        return len(a)==len(b) and all(close_values(x,y) for x,y in zip(a,b))
    return a==b


def glyphs(page,clip):
    result=[];names=set()
    for span in page.get_texttrace():
        for code,gid,origin,bbox in span['chars']:
            r=fitz.Rect(bbox)
            if not r.intersects(clip): continue
            names.add(span['font'])
            result.append((code,gid,relative(fitz.Point(origin),clip),relative(r,clip),
                           span['font'],span['size'],span['type'],
                           tuple(c*1000 for c in span['color']),span.get('opacity',1)*1000))
    return result,names


def fonts(page,names):
    result=[];covered=set()
    key=lambda name:re.sub(r'[^a-z0-9]','',re.sub(r'^[A-Z]{6}\+','',name).lower())
    for font in page.get_fonts(full=True):
        name=re.sub(r'^[A-Z]{6}\+','',font[3])
        _,extension,kind,buffer=page.parent.extract_font(font[0])
        matched={n for n in names if key(n)==key(name)}
        if not matched and buffer:
            # A PDF PostScript name can differ from the embedded font's family
            # name used by get_texttrace. Resolve it without logging either name.
            matched={n for n in names if key(n)==key(fitz.Font(fontbuffer=buffer).name)}
        if not matched: continue
        if kind=='Type3': raise PDFError('visual_font_validation_unavailable')
        covered.update(matched)
        # Internal only: bytes must stay identical, not merely the font's name.
        result.append((name,kind,extension,hashlib.sha256(buffer).digest()))
    if covered!=names: raise PDFError('visual_font_validation_unavailable')
    return sorted(set(result))


def paths(page,clip):
    result=[]
    for path in page.get_drawings():
        r=fitz.Rect(path['rect'])
        # Horizontal/vertical lines have zero-area bounding boxes. Rect.intersects
        # would silently exclude precisely the rules/minus signs we must protect.
        margin=max((path.get('width') or 0)/2,.001)
        if not fitz.Rect(r.x0-margin,r.y0-margin,r.x1+margin,r.y1+margin).intersects(clip): continue
        result.append((relative(path['items'],clip),path['type'],path.get('closePath'),
                       path.get('width'),path.get('dashes'),path.get('lineCap'),path.get('lineJoin'),
                       tuple(c*1000 for c in (path.get('color') or ())),
                       tuple(c*1000 for c in (path.get('fill') or ())),
                       (path.get('stroke_opacity') or 0)*1000,(path.get('fill_opacity') or 0)*1000,path.get('even_odd')))
    return result


def clipped_paths(page,clip):
    """Conservative proof for original_text, not a raster-only exception.

    get_drawings() returns paint commands inside *all* imported Forms, including
    those hidden by another asset's Form BBox. Extended drawing levels/scissors
    let us prove those commands cannot paint this asset. Scissors are only outer
    bounds: an intersecting non-rectangular clip or transparency group is NOT
    interpreted as a rectangle. Unsupported visible scopes fail closed.

    Retain the complete geometry/style of every potentially visible path. In
    particular a boundary-crossing line is never shortened, rasterized or
    discarded just because its centreline/bounding box is outside the crop.
    """
    result=[]; scopes=[]; ignored=0
    paint_bounds=page.get_bboxlog()
    for path in page.get_drawings(extended=True):
        level=path['level']
        while scopes and scopes[-1]['level']>=level: scopes.pop()
        if path['type'] in ('clip','group'):
            scopes.append(path)
            continue
        effective=fitz.Rect(clip) & page.rect
        for scope in scopes:
            if scope['type']=='clip': effective &= fitz.Rect(scope['scissor'])
        # The scissor is a conservative bound even for curves or nested Forms.
        # Empty intersection is proof of invisibility, independent of paint.
        if effective.is_empty:
            ignored+=1
            continue
        r=fitz.Rect(path['rect']); margin=max((path.get('width') or 0)/2,.001)
        r=fitz.Rect(r.x0-margin,r.y0-margin,r.x1+margin,r.y1+margin)
        # Include MuPDF's paint bounds, not only the path centreline: acute
        # miter joins and caps can paint beyond half the stroke width. For fs,
        # fill/stroke can occupy consecutive paint sequence entries.
        seq=path['seqno']
        for index in (seq,seq+1) if path['type']=='fs' else (seq,):
            if index<len(paint_bounds) and paint_bounds[index][0] in ('fill-path','stroke-path'):
                r |= fitz.Rect(paint_bounds[index][1])
        if not r.intersects(clip): continue
        for scope in scopes:
            if scope['type']=='group': return None,ignored
            items=scope['items']
            if len(items)!=1 or items[0][0]!='re': return None,ignored
        # Compare both native paint and its effective visible rectangular clip.
        signature=(relative(path['items'],clip),path['type'],path.get('closePath'),
                   path.get('width'),path.get('dashes'),path.get('lineCap'),path.get('lineJoin'),
                   tuple(c*1000 for c in (path.get('color') or ())),
                   tuple(c*1000 for c in (path.get('fill') or ())),
                   (path.get('stroke_opacity') or 0)*1000,(path.get('fill_opacity') or 0)*1000,path.get('even_odd'))
        result.append((signature,relative(effective,clip)))
    return result,ignored


def images(page,clip):
    return [(i['digest'],i['width'],i['height'],relative(fitz.Rect(i['bbox']),clip))
            for i in page.get_image_info(hashes=True) if fitz.Rect(i['bbox']).intersects(clip)]


def safe_visual_metadata(asset):
    clip=fitz.Rect(asset['source_bbox']);target=fitz.Rect(asset.get('japanese_bbox',{}).get('rect',clip))
    return {'asset_id':asset['id'],'asset_kind':asset['kind'] if asset['kind'] in KINDS else 'figure',
            'source_page':asset['source_page'],'japanese_page':asset.get('japanese_page'),
            'source_size_pt':[round(clip.width,4),round(clip.height,4)],
            'output_size_pt':[round(target.width,4),round(target.height,4)],
            'dpi':DPI,'vector_preserved':asset.get('vector_preserved'),
            'annotations_present':bool(asset.get('rasterize_annotations')),
            'quality_policy':'original_text' if asset['kind']=='original_text' else 'scientific_visual',
            'status':'failed','error':None,'classification':None}


def inspect_visual(source,output,asset):
    report=safe_visual_metadata(asset)
    page=None;rotation=None;annotation_free=None
    try:
        page=source[asset['source_page']-1]
        rotation=page.rotation
        report.update(source_rotation=page.rotation,
                      cropbox_size_pt=[round(page.cropbox.width,4),round(page.cropbox.height,4)])
        page.set_rotation(0)
        clip=fitz.Rect(asset['source_bbox']);target=fitz.Rect(asset['japanese_bbox']['rect'])
        dest=output[asset['japanese_page']-1]
        report['native_size_preserved']=close_values((clip.width,clip.height),(target.width,target.height))
        a=canonical_pixmap(page,clip);b=canonical_pixmap(dest,target)
        report.update(source_image_size_px=[a.width,a.height],output_image_size_px=[b.width,b.height])
        report.update(source_ink_pixels=ink_pixels(a),output_ink_pixels=ink_pixels(b))
        mean=pixel_mean(a,b)
        # Keep ordinary page-space comparison as a diagnostic, not as sole proof.
        ra=page.get_pixmap(clip=clip,dpi=DPI,colorspace=fitz.csRGB,alpha=False,annots=True)
        rb=dest.get_pixmap(clip=target,dpi=DPI,colorspace=fitz.csRGB,alpha=False,annots=True)
        raw=pixel_mean(ra,rb)
        report.update(mean_pixel_difference=round(mean,6) if mean is not None else None,
                      raw_mean_pixel_difference=round(raw,6) if raw is not None else None,
                      raw_source_image_size_px=[ra.width,ra.height],raw_output_image_size_px=[rb.width,rb.height])
        # get_drawings/get_texttrace include annotation appearances. The base
        # vectors are compared separately from the explicitly flattened overlay.
        content_page=page
        if asset.get('rasterize_annotations'):
            annotation_free=fitz.open()
            annotation_free.insert_pdf(source,from_page=page.number,to_page=page.number,annots=False)
            content_page=annotation_free[0]
        report['placed_text']=text_counts(content_page.get_text('text',clip=clip),dest.get_text('text',clip=target))
        report['source_text_preserved']=report['placed_text']['order_matches']
        report['crop_text_diagnostics']=asset.get('crop_text_diagnostics',{})
        report['original_block_chars']=asset.get('original_block_chars')
        report['crop_no_hidden_text']=asset.get('crop_no_hidden_text',True)
        report['crop_text_complete']=asset.get('crop_text_complete',True)
        sg,sn=glyphs(content_page,clip);dg,dn=glyphs(dest,target)
        sp,dp=paths(content_page,clip),paths(dest,target)
        si,di=images(content_page,clip),images(dest,target)
        glyph_codes_preserved=[g[:2] for g in sg]==[g[:2] for g in dg]
        crop_checks=report['crop_text_diagnostics']
        report['text_failure_stage']=(
            'outside_text_redaction' if crop_checks.get('after_outside_redaction',{}).get('order_matches') is False else
            'native_crop' if crop_checks.get('native_crop',{}).get('order_matches') is False else
            'hidden_crop_text' if crop_checks.get('native_crop_all_text',{}).get('order_matches') is False else
            'pdf_placement' if not report['source_text_preserved'] else
            'glyph_mapping' if not glyph_codes_preserved else None)
        delta=max((abs(x-y) for a,b in zip(sg,dg) for x,y in zip(a[2],b[2])),default=0.) if len(sg)==len(dg) else None
        report.update(source_glyph_count=len(sg),output_glyph_count=len(dg),glyphs_preserved=close_values(sg,dg),
                      glyph_codes_preserved=glyph_codes_preserved,
                      max_glyph_origin_difference_pt=round(delta,6) if delta is not None else None,
                      fonts_preserved=fonts(content_page,sn)==fonts(dest,dn),
                      source_drawing_count=len(sp),output_drawing_count=len(dp),drawings_preserved=close_values(sp,dp),
                      source_image_count=len(si),output_image_count=len(di),images_preserved=close_values(si,di))
        # A declared annotation raster adds an appearance image. Its original
        # content is separately checked below; source vectors/glyphs remain intact.
        raster=asset.get('rasterize_annotations')
        if raster:
            report['images_preserved']=close_values(si,di[:-1]) if len(di)==len(si)+1 else False
            report['annotation_raster_verified']=(asset.get('annotation_raster_verified',False) and
                len(di)==len(si)+1 and di[-1][0]==asset.get('_annotation_digest'))
        sv,signored=clipped_paths(content_page,clip);dv,dignored=clipped_paths(dest,target)
        available=sv is not None and dv is not None
        visible_same=available and close_values(sv,dv)
        report.update(visible_drawings_verified=available,visible_drawings_preserved=visible_same,
                      source_invisible_drawing_count=signored,output_invisible_drawing_count=dignored)
        drawings_valid=report['drawings_preserved'] and visible_same
        if asset['kind']=='original_text':
            # A full-structure mismatch can be waived ONLY with a clip proof,
            # strict image/content checks below and byte-identical rendered RGB.
            # The scientific_visual policy continues to require full structure.
            report.update(exact_pixels_preserved=mean==0,
                          nonvisible_drawing_difference_proven=(not report['drawings_preserved'] and visible_same and mean==0))
            drawings_valid=visible_same and (report['drawings_preserved'] or mean==0)
        if not report['native_size_preserved'] or mean is None:
            report.update(error='visual_geometry_changed',classification='clip_geometry')
        elif not report['crop_no_hidden_text']:
            # The legacy flag is an equality check: missing text also makes it
            # false. Do not misdiagnose a boundary deletion as text contamination.
            counts=crop_checks.get('native_crop_all_text',{})
            classification=('unexpected_text' if counts.get('extra_chars',0)>0 or not counts else
                            'text_missing_or_changed' if counts.get('missing_chars',0)>0 else 'text_order_changed')
            report.update(error='visual_text_changed',classification=classification)
        elif not report['crop_text_complete'] or not report['source_text_preserved']:
            report.update(error='visual_text_changed',classification='text_missing_or_changed')
        elif not report['fonts_preserved']:
            report.update(error='visual_content_changed',classification='font_resource_changed')
        elif not report['glyph_codes_preserved']:
            report.update(error='visual_text_changed',classification='text_missing_or_changed')
        elif not report['glyphs_preserved']:
            report.update(error='visual_content_changed',classification='glyph_geometry_changed')
        elif not drawings_valid or not report['images_preserved']:
            classification=('drawing_and_image_changed' if not drawings_valid and not report['images_preserved']
                            else 'drawing_changed' if not drawings_valid else 'image_changed')
            report.update(error='visual_content_changed',classification=classification)
        elif raster and not report['annotation_raster_verified']:
            report.update(error='visual_content_changed',classification='annotation_appearance_changed')
        elif mean>PIXEL_LIMIT:
            report.update(error='visual_content_changed',classification='rendered_content_changed')
        else:
            classification=('nonvisible_drawing_commands' if report.get('nonvisible_drawing_difference_proven')
                            else 'raster_alignment' if raw is None or raw>PIXEL_LIMIT else 'native_content_preserved')
            report.update(status='passed',error=None,classification=classification)
    except PDFError as error:
        report.update(error=str(error),classification='validation_limit' if str(error)=='visual_too_large_for_validation' else 'validation_unavailable')
    except Exception:
        report.update(error='visual_validation_failed',classification='validation_unavailable')
    finally:
        if annotation_free is not None: annotation_free.close()
        if page is not None and rotation is not None: page.set_rotation(rotation)
    return report


def visual_quality(source,output,asset):
    report=inspect_visual(source,output,asset)
    if report['status']=='failed': raise VisualQualityError([report])
    return report
