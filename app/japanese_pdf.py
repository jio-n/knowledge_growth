"""Readable reflow PDF, intact vector visuals, and per-artifact source alignment.

Original bytes are never redacted. Text flows through Story at fixed readable
sizes; visuals and captions are indivisible groups. Validation uses content,
geometry, reading order and cropped visual fidelity, not original-page pixels.
"""
from collections import Counter
import html
import io
import math
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import fitz
from PIL import Image, ImageChops
from .config import ROOT
from .japanese_content import detect_units
from .japanese_translation import PDFError

LAYOUT_VERSION='reflow-1'
WIDTH,HEIGHT,MARGIN=595.,842.,44.
BOTTOM=HEIGHT-MARGIN
BODY_SIZE=11.
CAPTION_SIZE=10.
DPI=200
MAX_PAGES=64
MAX_VISUAL_PIXELS=16_000_000


def norm(text): return re.sub(r'\s+','',text)


def geometry(page,rect):
    return {'coordinate_system':'pymupdf_unrotated','units':'pt','rect':list(rect),
            'page_rect':[0,0,page.rect.width,page.rect.height],'rotation':0}


def text_bounds(page):
    boxes=[fitz.Rect(s['bbox']) for b in page.get_text('dict')['blocks'] if b['type']==0
           for line in b['lines'] for s in line['spans'] if s['text'].strip()]
    if not boxes: raise PDFError('text_layout_empty')
    result=fitz.Rect(boxes[0])
    for r in boxes[1:]: result |= r
    return result


def text_document(text,width,archive,size=BODY_SIZE,first_y=MARGIN,height=HEIGHT):
    # Fixed sizes. Story emits subsequent pages when a paragraph exceeds the
    # first available rectangle; no scale-to-fit or truncation is permitted.
    css=('@font-face {font-family:KGJapanese;src:url(kg-cjk.ttf);}'
         f'body {{margin:0;font-family:KGJapanese;font-size:{size}pt;line-height:1.6;}}'
         'div {padding-top:3pt;padding-bottom:5pt;margin:0;}')
    story=fitz.Story(html='<div lang="ja">'+html.escape(text)+'</div>',user_css=css,archive=archive)
    def rectfn(number,filled):
        if number>=MAX_PAGES: raise PDFError('output_page_limit')
        return fitz.Rect(0,0,width,height),fitz.Rect(MARGIN,first_y if number==0 else MARGIN,width-MARGIN,height-MARGIN),None
    return story.write_with_links(rectfn)


def cropped_visual(source,clip):
    # A show_pdf_page clip alone retains an entire source-page Form XObject.
    # PDF.js extracts text outside that clip. Replay only intersecting display-list
    # operations into a new vector PDF before placement, so no hidden body/caption
    # survives in the copied resource. The original document is never changed.
    stream=io.BytesIO();writer=fitz.DocumentWriter(stream)
    rect=fitz.Rect(0,0,clip.width,clip.height)
    device=writer.begin_page(rect)
    # PyMuPDF device wrappers differ across supported versions; DisplayList.run
    # expects .device while DocumentWriter may return a .this wrapper.
    adapter=device if hasattr(device,'device') else SimpleNamespace(device=device.this)
    source.get_displaylist(annots=False).run(adapter,fitz.Matrix(1,0,0,1,-clip.x0,-clip.y0),rect)
    writer.end_page();writer.close()
    cropped=fitz.open(stream=stream.getvalue(),filetype='pdf')
    page=cropped[0]
    # One text-paint operation can span both inside and outside the clip. Remove
    # those outside glyphs from this disposable crop, never from the source PDF.
    spans=[s for b in page.get_text('dict',clip=fitz.INFINITE_RECT())['blocks'] if b['type']==0
           for line in b['lines'] for s in line['spans']]
    if any(not rect.contains(fitz.Rect(s['bbox'])) for s in spans):
        outer=fitz.Rect(rect)
        for s in spans: outer |= fitz.Rect(s['bbox'])
        outer=fitz.Rect(outer.x0-2,outer.y0-2,outer.x1+2,outer.y1+2)
        outside=[fitz.Rect(outer.x0,outer.y0,0,outer.y1),fitz.Rect(rect.x1,outer.y0,outer.x1,outer.y1),
                 fitz.Rect(outer.x0,outer.y0,outer.x1,0),fitz.Rect(outer.x0,rect.y1,outer.x1,outer.y1)]
        for area in outside:
            if not area.is_empty: page.add_redact_annot(area,fill=False,cross_out=False)
        page.apply_redactions(images=0,graphics=0,text=0)
    return cropped


class Reflow:
    def __init__(self,original,output,units,archive,font):
        self.original,self.output,self.units,self.archive,self.font=original,output,units,archive,font
        self.by_id={u['block']['id']:u for u in units}
        self.page=None; self.y=MARGIN; self.assets=[]; self.flow=[]
        self.new_page()

    def new_page(self,width=WIDTH,height=HEIGHT):
        if self.page is not None and self.y==MARGIN:
            self.output.delete_page(self.page.number)
        if len(self.output)>=MAX_PAGES: raise PDFError('output_page_limit')
        self.page=self.output.new_page(width=width,height=height)
        self.y=MARGIN

    def record(self,unit,page,rect,part=0):
        unit.setdefault('placements',[]).append({'japanese_page':page.number+1,
            'japanese_bbox':geometry(page,rect),'part':part})

    def japanese(self,unit):
        text=unit.get('text','')
        if unit['status']!='pending': return False
        if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]',text):
            unit.update(status='failed',reason='cached_not_japanese'); return False
        if any(c in '□■�' or (not c.isspace() and not self.font.has_glyph(ord(c))) for c in text):
            unit.update(status='failed',reason='missing_glyph'); return False
        return True

    def paragraph(self,unit):
        if not self.japanese(unit):
            self.original_block(unit); return
        if self.page.rect.height-self.y-MARGIN<65: self.new_page()
        size=16. if unit['block']['kind']=='heading' else BODY_SIZE
        # Headings stay with the following paragraph by reserving a few lines.
        if unit['block']['kind']=='heading' and self.page.rect.height-self.y-MARGIN<130: self.new_page()
        with text_document(unit['text'],self.page.rect.width,self.archive,size,self.y,self.page.rect.height) as text_pdf:
            for part,temp in enumerate(text_pdf):
                if part: self.new_page()
                rect=text_bounds(temp)
                self.page.show_pdf_page(self.page.rect,text_pdf,part)
                self.record(unit,self.page,rect,part)
                self.y=rect.y1+12
                self.flow.append({'kind':'text','block_ids':[unit['block']['id']],
                                  'page':self.page.number+1,'bbox':list(rect)})
        unit.update(status='translated',reason=None,placed_font_size=size)

    def original_block(self,unit):
        b=unit['block']
        if not b.get('bbox'): raise PDFError('missing_fallback_geometry')
        asset={'id':'U'+b['id'][:24],'source_page':b['page'],'source_bbox':b['bbox']['rect'],
               'kind':'original_text','original_block_ids':[b['id']],'caption_block_ids':[],
               'status':'unresolved','reason':unit['reason'] or 'translation_failed'}
        self.visual(asset)

    def visual(self,asset):
        source=self.original[asset['source_page']-1]
        source.set_rotation(0)  # in-memory copy only; unrotated source contract
        clip=fitz.Rect(asset['source_bbox'])
        captions=[self.by_id[bid] for bid in asset['caption_block_ids']]
        captions_text=[]
        for unit in captions:
            if self.japanese(unit): captions_text.append(unit['text'])
            else:
                # Uncertain translation is explicitly English, but remains next
                # to its original visual. No caption is dropped or reassigned.
                captions_text.append(unit['block']['text'])
        # A wide/tall visual gets a dedicated page at its original point size.
        width=max(WIDTH,clip.width+MARGIN*2+1)
        caption_height=0
        for text in captions_text:
            with text_document(text,width,self.archive,CAPTION_SIZE,MARGIN,height=14000) as measured:
                caption_height+=text_bounds(measured[0]).height+18
        group_height=clip.height+caption_height+12+(35 if asset["status"]=="unresolved" else 0)
        dedicated=width>WIDTH or group_height>HEIGHT-MARGIN*2 or self.page.rect.width!=WIDTH
        height=max(HEIGHT,group_height+MARGIN*2+4)
        if dedicated and not (self.page.rect.width==width and self.y+group_height<=self.page.rect.height-MARGIN):
            self.new_page(width,height)
        elif self.y+group_height>self.page.rect.height-MARGIN: self.new_page()
        if asset['status']=='unresolved':
            with text_document(f"未解決・原物保持（原文p.{asset['source_page']}）",self.page.rect.width,self.archive,9.,self.y,self.page.rect.height) as notice:
                if len(notice)!=1: raise PDFError('visual_notice_split')
                rect=text_bounds(notice[0]); self.page.show_pdf_page(self.page.rect,notice,0)
                self.flow.append({'kind':'notice','block_ids':[],'page':self.page.number+1,'bbox':list(rect)})
                self.y=rect.y1+8
        # Match the source's subpixel raster phase at validation DPI while moving
        # it; source vectors and embedded images are copied without resampling.
        scale=DPI/72
        x=clip.x0+math.ceil((MARGIN-clip.x0)*scale)/scale
        y=clip.y0+math.ceil((self.y-clip.y0)*scale)/scale
        target=fitz.Rect(x,y,x+clip.width,y+clip.height)
        with cropped_visual(source,clip) as visual_pdf:
            self.page.show_pdf_page(target,visual_pdf,0)
        if asset.get('rasterize_annotations'):
            # show_pdf_page excludes annotations. Overlay the original rendered
            # appearance while retaining the same vector text for selection.
            raster=source.get_pixmap(clip=clip,dpi=DPI,colorspace=fitz.csRGB,alpha=False,annots=True)
            self.page.insert_image(target,stream=raster.tobytes('png'),keep_proportion=False)
        asset.update(japanese_page=self.page.number+1,japanese_bbox=geometry(self.page,target),vector_preserved=not asset.get('rasterize_annotations'))
        for bid in asset['original_block_ids']: self.record(self.by_id[bid],self.page,target)
        self.flow.append({'kind':'visual','asset_id':asset['id'],'block_ids':asset['original_block_ids'],
                          'page':self.page.number+1,'bbox':list(target)})
        self.y=target.y1+6
        for unit,text in zip(captions,captions_text):
            with text_document(text,self.page.rect.width,self.archive,CAPTION_SIZE,self.y,self.page.rect.height) as caption_pdf:
                if len(caption_pdf)!=1: raise PDFError('caption_group_split')
                rect=text_bounds(caption_pdf[0])
                self.page.show_pdf_page(self.page.rect,caption_pdf,0)
                self.record(unit,self.page,rect)
                self.flow.append({'kind':'caption','asset_id':asset['id'],'block_ids':[unit['block']['id']],
                                  'page':self.page.number+1,'bbox':list(rect)})
                self.y=rect.y1+12
                if unit['status']=='pending': unit.update(status='translated',reason=None,placed_font_size=CAPTION_SIZE)
            asset['caption_status']='translated' if all(u['status']=='translated' for u in captions) else 'untranslated'
        self.y+=10
        self.assets.append(asset)
        if dedicated: self.new_page()


def visual_quality(source,output,asset):
    page=source[asset['source_page']-1]; page.set_rotation(0)
    clip=fitz.Rect(asset['source_bbox']); target=fitz.Rect(asset['japanese_bbox']['rect'])
    if clip.width*clip.height*(DPI/72)**2>MAX_VISUAL_PIXELS: raise PDFError('visual_too_large_for_validation')
    a=page.get_pixmap(clip=clip,dpi=DPI,colorspace=fitz.csRGB,alpha=False,annots=True)
    b=output[asset['japanese_page']-1].get_pixmap(clip=target,dpi=DPI,colorspace=fitz.csRGB,alpha=False,annots=True)
    if (a.width,a.height)!=(b.width,b.height): raise PDFError('visual_geometry_changed')
    ia=Image.frombytes('RGB',(a.width,a.height),a.samples); ib=Image.frombytes('RGB',(b.width,b.height),b.samples)
    diff=ImageChops.difference(ia,ib)
    hist=diff.histogram(); mean=sum((i%256)*count for i,count in enumerate(hist))/(a.width*a.height*3)
    if mean>0.2: raise PDFError('visual_content_changed')
    # Text drawn inside vectors remains searchable with the same numerical/model
    # contents. This supplements pixel fidelity; no table/VLM rewriting is used.
    if norm(page.get_text('text',clip=clip))!=norm(output[asset['japanese_page']-1].get_text('text',clip=target)):
        raise PDFError('visual_text_changed')
    return {'asset_id':asset['id'],'dpi':DPI,'mean_pixel_difference':round(mean,6),
            'source_text_preserved':True,'native_size_preserved':True}


def validate_output(original,pdf,units,assets,flow):
    text_count=0
    for unit in units:
        if not unit.get('placements'): raise PDFError('unplaced_source_block')
        if unit['status']=='translated':
            fragments=[pdf[p['japanese_page']-1].get_text('text',clip=fitz.Rect(p['japanese_bbox']['rect'])) for p in unit['placements']]
            if norm(''.join(fragments))!=norm(unit['text']): raise PDFError('text_missing_or_duplicate')
            text_count+=1
    previous=(0,0.)
    for item in flow:
        page=pdf[item['page']-1]; rect=fitz.Rect(item['bbox'])
        if not fitz.Rect(MARGIN-1,MARGIN-1,page.rect.width-MARGIN+1,page.rect.height-MARGIN+1).contains(rect):
            raise PDFError('content_outside_page')
        if (item['page'],rect.y0)<previous: raise PDFError('reading_order_changed')
        previous=(item['page'],rect.y0)
    for i,item in enumerate(flow):
        for other in flow[i+1:]:
            if item['page']==other['page'] and fitz.Rect(item['bbox']).intersects(fitz.Rect(other['bbox'])):
                raise PDFError('layout_overlap')
    for asset in assets:
        for bid in asset['caption_block_ids']:
            unit=next(u for u in units if u['block']['id']==bid)
            if any(p['japanese_page']!=asset['japanese_page'] for p in unit['placements']): raise PDFError('caption_group_split')
    quality=[visual_quality(original,pdf,a) for a in assets]
    return {'text_blocks_validated':text_count,'missing_or_duplicate':0,'overlaps':0,
            'outside_page':0,'reading_order_preserved':True,'visuals':quality}


def generate_pdf(original,units,output_path):
    with fitz.open(stream=original,filetype='pdf') as source,fitz.open() as output:
        font=fitz.Font('cjk')
        with tempfile.TemporaryDirectory(prefix='kg-reflow-font-') as directory:
            Path(directory,'kg-cjk.ttf').write_bytes(font.buffer)
            layout=Reflow(source,output,units,fitz.Archive(directory),font)
            ordered=sorted((u for u in units if 'layout_item' in u),key=lambda u:u['layout_item']['order'])
            section=''
            for index,owner in enumerate(ordered):
                item=owner['layout_item']
                if item['kind']=='text' and owner['block']['kind']=='heading' and index+1<len(ordered):
                    following=ordered[index+1]['layout_item']
                    if following['kind']=='asset':
                        asset=following['asset']; clip=fitz.Rect(asset['source_bbox'])
                        width=max(WIDTH,clip.width+MARGIN*2+1)
                        caption_chars=sum(len(layout.by_id[b].get('text') or layout.by_id[b]['block']['text']) for b in asset['caption_block_ids'])
                        reserve=clip.height+math.ceil(caption_chars/max(1,(width-MARGIN*2)/CAPTION_SIZE))*CAPTION_SIZE*1.6+110
                        if width!=layout.page.rect.width or layout.y+reserve>layout.page.rect.height-MARGIN:
                            layout.new_page(width,max(HEIGHT,reserve+MARGIN*2))
                if item['kind']=='text':
                    if owner['block']['kind']=='heading': section=owner['block'].get('heading_path') or owner['block']['text']
                    owner['section']=section or owner['block'].get('heading_path','')
                    layout.paragraph(owner)
                else:
                    for bid in item['asset']['original_block_ids']+item['asset']['caption_block_ids']:
                        layout.by_id[bid]['section']=section or layout.by_id[bid]['block'].get('heading_path','')
                    layout.visual(item['asset'])
            if not any(u['status']=='translated' for u in units): raise PDFError('no_safe_translated_blocks')
            if layout.y==MARGIN and len(output)>1: output.delete_page(len(output)-1)
        for page in output:
            page.insert_font(fontname='KGJapaneseNotice',fontbuffer=font.buffer)
            page.insert_text((MARGIN,26),'日本語再組版・試作（原文の選択ページのみ収録）',fontname='KGJapaneseNotice',fontsize=8,color=(.4,.4,.4))
            page.insert_text((MARGIN,page.rect.height-18),f"knowledge_growth | {page.number+1}",fontsize=8,color=(.4,.4,.4))
        toc=[]; previous_level=0
        for unit in units:
            if unit['status']=='translated' and unit['block']['kind']=='heading':
                level=min(max(1,unit['block'].get('level') or 1),previous_level+1)
                toc.append([level,unit['text'],unit['placements'][0]['japanese_page']])
                previous_level=level
        if toc: output.set_toc(toc)
        output.embfile_add('knowledge_growth-japanese-font-NOTICE.txt',
            (ROOT/'docs/licenses/droid-NOTICE.txt').read_bytes(),desc='Droid Sans Fallback: Apache 2.0 license and attribution')
        output.set_metadata({'title':'日本語再組版・選択ページ試作','creator':'knowledge_growth reflow-1'})
        output.save(output_path,garbage=4,deflate=True)
    with fitz.open(stream=original,filetype='pdf') as source,fitz.open(output_path) as completed:
        quality=validate_output(source,completed,units,layout.assets,layout.flow)
        translated=[u for u in units if u['status']=='translated']
        text_blocks=sum(bool(u['block']['text'].strip()) and u['kind']!='visual' for u in units)
        return {'layout_version':LAYOUT_VERSION,'selected_pages':sorted({u['block']['page'] for u in units}),
                'japanese_page_count':len(completed),'original_page_count':len(source),
                'counts':dict(Counter(u['status'] for u in units)),'text_blocks':text_blocks,
                'source_text_blocks':sum(bool(u['block']['text'].strip()) for u in units),
                'translated_ratio':len(translated)/text_blocks if text_blocks else 0,
                'translated_pages':sorted({p['japanese_page'] for u in translated for p in u['placements']}),
                'quality':quality,'assets':layout.assets,'reading_order':[bid for item in layout.flow for bid in item['block_ids']],
                'units':[{'block_id':u['block']['id'],'translation_id':u.get('translation_id'),
                          'page':u['block']['page'],'bbox':u['block'].get('bbox'),
                          'section':u.get('section',u['block'].get('heading_path','')),'kind':u['kind'],'asset_id':u['asset_id'],
                          'status':u['status'],'reason':u['reason'],'font_size':u.get('placed_font_size'),
                          'placements':u.get('placements',[])} for u in units]}
