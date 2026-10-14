"""Readable reflow PDF, intact vector visuals, and per-artifact source alignment.

Original bytes are never redacted. Text flows through Story at fixed readable
sizes; visuals and captions are indivisible groups. Validation uses content,
geometry, reading order and cropped visual fidelity, not original-page pixels.
"""
from collections import Counter
import html
import math
from pathlib import Path
import re
import tempfile
import fitz
from .config import ROOT
from .japanese_content import detect_units
from .japanese_semantics import (ASSEMBLY_VERSION,source_members,report_units,semantic_report,coverage,reading_quality)
from .japanese_translation import PDFError
from .japanese_visual_quality import (cropped_visual, visual_quality, VisualQualityError,
    canonical_pixmap, safe_visual_metadata, VISUAL_QUALITY_VERSION, DPI, MAX_VISUAL_PIXELS)
from .japanese_extraction import EXTRACTION_VERSION
from .japanese_textflow import CLASSIFICATION_VERSION
from .japanese_overlap import OVERLAP_VERSION,content_report

LAYOUT_VERSION='reflow-7'
WIDTH,HEIGHT,MARGIN=595.,842.,44.
BOTTOM=HEIGHT-MARGIN
BODY_SIZE=11.
CAPTION_SIZE=10.
MAX_PAGES=64


def norm(text):
    # Story may shape ASCII fi/fl into a standard Unicode ligature. Restrict
    # equivalence to these letter glyphs; never normalize scientific numbers,
    # symbols or arbitrary compatibility characters.
    text=text.translate(str.maketrans(dict(zip('ﬀﬁﬂﬃﬄﬅﬆ',('ff','fi','fl','ffi','ffl','st','st')))))
    return re.sub(r'\s+','',text)


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


def balanced_title(text,font,width,size):
    """Balance long title lines at word/CJK boundaries without shrinking type."""
    length=font.text_length(text,fontsize=size)
    lines=math.ceil(length/width)
    if lines<=1:return text
    result=[];remaining=text
    forbidden='、。，．：；？！）］｝」』ぁぃぅぇぉっゃゅょァィゥェォッャュョー'
    for left in range(lines,1,-1):
        target=font.text_length(remaining,fontsize=size)/left
        candidates=[]
        for i in range(1,len(remaining)):
            a,b=remaining[i-1],remaining[i]
            if b in forbidden:continue
            if ((re.match(r'[\u30a0-\u30ff]',a) and re.match(r'[\u30a0-\u30ff]',b)) or
                    (re.match(r'[\u3400-\u9fff]',a) and re.match(r'[\u3400-\u9fff]',b))):continue
            if not (a.isspace() or a in '：:、' or (re.match(r'[\u3040-\u9fff]',a) and re.match(r'[\u3040-\u9fff]',b))):continue
            used=font.text_length(remaining[:i],fontsize=size)
            if used>width:break
            candidates.append((abs(used-target)-(size*.6 if a in 'とのをにはがで' else 0),i))
        if not candidates:break
        cut=min(candidates)[1];result.append(remaining[:cut].rstrip());remaining=remaining[cut:].lstrip()
    return '\n'.join(result+[remaining])


def text_document(text,width,archive,size=BODY_SIZE,first_y=MARGIN,height=HEIGHT,line_height=1.6,compact=False,untranslated=False,break_lines=False,first_bottom=0):
    # Fixed sizes. Story emits subsequent pages when a paragraph exceeds the
    # first available rectangle; no scale-to-fit or truncation is permitted.
    css=('@font-face {font-family:KGJapanese;src:url(kg-cjk.ttf);}'
         f'body {{margin:0;font-family:KGJapanese;font-size:{size}pt;line-height:{line_height};}}'
         +('div {padding-top:3pt;padding-bottom:0;margin:0;}' if compact else 'div {padding-top:3pt;padding-bottom:5pt;margin:0;}'))
    marker='<span style="font-size:9pt;color:#666">原文（未翻訳）</span><br>' if untranslated else ''
    escaped=html.escape(text)
    if break_lines:escaped=escaped.replace('\n','<br>')
    story=fitz.Story(html='<div lang="ja">'+marker+escaped+'</div>',user_css=css,archive=archive)
    def rectfn(number,filled):
        if number>=MAX_PAGES: raise PDFError('output_page_limit')
        return fitz.Rect(0,0,width,height),fitz.Rect(MARGIN,first_y if number==0 else MARGIN,width-MARGIN,height-MARGIN-(first_bottom if number==0 else 0)),None
    return story.write_with_links(rectfn)


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

    def label(self,text):
        if self.page.rect.height-self.y-MARGIN<80:self.new_page()
        with text_document(text,self.page.rect.width,self.archive,9.,self.y,self.page.rect.height,1.25,True) as doc:
            if len(doc)!=1:raise PDFError('metadata_label_split')
            r=text_bounds(doc[0]);self.page.show_pdf_page(self.page.rect,doc,0)
            self.flow.append({'kind':'metadata_label','block_ids':[],'page':self.page.number+1,'bbox':list(r)})
            self.y=r.y1+5

    def japanese(self,unit):
        text=unit.get('text','')
        if unit['status']!='pending': return False
        if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]',text):
            unit.update(status='failed',reason='cached_not_japanese'); return False
        if any(c in '□■�' or (not c.isspace() and not self.font.has_glyph(ord(c))) for c in text):
            unit.update(status='failed',reason='missing_glyph'); return False
        return True

    def paragraph(self,unit):
        role=unit.get('semantic_role')
        translated=self.japanese(unit) if unit['kind']!='metadata' else False
        text=unit['text'] if translated else unit['block']['text']
        if not text.strip() and role=='page_decoration':
            self.record(unit,self.page,fitz.Rect(MARGIN,self.y,MARGIN,self.y))
            unit['_rendered_text']='';return
        if any(not c.isspace() and not self.font.has_glyph(ord(c)) for c in text):raise PDFError('missing_source_glyph')
        untranslated=not translated and unit['kind']!='metadata'
        unit['_rendered_text']=('原文（未翻訳）\n' if untranslated else '')+text
        if self.page.rect.height-self.y-MARGIN<65: self.new_page()
        size=20. if role=='paper_title' else 16. if role in ('abstract_heading','section_heading') else BODY_SIZE
        if role=='paper_title':text=balanced_title(text,self.font,self.page.rect.width-MARGIN*2,size)
        # Headings stay with the following paragraph by reserving a few lines.
        if role in ('abstract_heading','section_heading') and self.page.rect.height-self.y-MARGIN<130: self.new_page()
        def compose(first_bottom=0):
            return text_document(text,self.page.rect.width,self.archive,size,self.y,self.page.rect.height,
                                 1.25 if role=='paper_title' or unit['kind']=='metadata' else 1.6,
                                 unit['kind']=='metadata',untranslated,role=='paper_title',first_bottom)
        text_pdf=compose()
        if len(text_pdf)>1:
            lines=lambda p:sum(len(b['lines']) for b in p.get_text('dict')['blocks'] if b['type']==0)
            if lines(text_pdf[0])<=2:
                text_pdf.close();self.new_page();text_pdf=compose()
            elif lines(text_pdf[-1])==1:
                text_pdf.close();text_pdf=compose(size*1.6)
        with text_pdf:
            for part,temp in enumerate(text_pdf):
                if part: self.new_page()
                rect=text_bounds(temp)
                self.page.show_pdf_page(self.page.rect,text_pdf,part)
                self.record(unit,self.page,rect,part)
                self.y=rect.y1+(3 if unit['kind']=='metadata' else 12)
                self.flow.append({'kind':'text','block_ids':[unit['block']['id']],
                                  'page':self.page.number+1,'bbox':list(rect)})
        unit.update(placed_font_size=size)
        if translated:unit.update(status='translated',reason=None)

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
        group_height=clip.height+caption_height+12
        dedicated=width>WIDTH or group_height>HEIGHT-MARGIN*2 or self.page.rect.width!=WIDTH
        height=max(HEIGHT,group_height+MARGIN*2+4)
        if dedicated and not (self.page.rect.width==width and self.y+group_height<=self.page.rect.height-MARGIN):
            self.new_page(width,height)
        elif self.y+group_height>self.page.rect.height-MARGIN: self.new_page()
        # Match the source's subpixel raster phase at validation DPI while moving
        # it; source vectors and embedded images are copied without resampling.
        scale=DPI/72
        x=clip.x0+math.ceil((MARGIN-clip.x0)*scale)/scale
        y=clip.y0+math.ceil((self.y-clip.y0)*scale)/scale
        target=fitz.Rect(x,y,x+clip.width,y+clip.height)
        try:
            crop_diagnostics={}
            asset['crop_text_diagnostics']=crop_diagnostics
            asset['original_block_chars']=sum(len(norm(b['text'])) for bid in asset['original_block_ids'] for b in source_members(self.by_id[bid]))
            with cropped_visual(source,clip,diagnostics=crop_diagnostics) as visual_pdf:
                if asset.get('rasterize_annotations'):
                    with fitz.open() as annotation_free:
                        annotation_free.insert_pdf(self.original,from_page=source.number,to_page=source.number,annots=False)
                        expected=norm(annotation_free[0].get_text('text',clip=clip))
                else: expected=norm(source.get_text('text',clip=clip))
                asset['crop_text_complete']=expected==norm(visual_pdf[0].get_text('text'))
                asset['crop_no_hidden_text']=expected==norm(visual_pdf[0].get_text('text',clip=fitz.INFINITE_RECT()))
                self.page.show_pdf_page(target,visual_pdf,0)
        except PDFError:
            diagnostic=safe_visual_metadata(asset)
            diagnostic.update(error='visual_crop_failed',classification='crop_unavailable')
            raise VisualQualityError([diagnostic]) from None
        if asset.get('rasterize_annotations'):
            # show_pdf_page excludes annotations. Overlay the original rendered
            # appearance while retaining the same vector text for selection.
            raster=canonical_pixmap(source,clip)
            encoded=raster.tobytes('png')
            decoded=fitz.Pixmap(encoded)
            asset['annotation_raster_verified']=(decoded.width,decoded.height,decoded.samples)==(raster.width,raster.height,raster.samples)
            asset['_annotation_digest']=decoded.digest  # in memory only, excluded from public assets
            # One source raster pixel per output validation pixel. Do not squeeze
            # a rounded pixel rectangle into a fractional point rectangle.
            raster_target=fitz.Rect(target.x0,target.y0,target.x0+raster.width/scale,target.y0+raster.height/scale)
            self.page.insert_image(raster_target,stream=encoded,keep_proportion=False)
        asset.update(japanese_page=self.page.number+1,japanese_bbox=geometry(self.page,target),vector_preserved=not asset.get('rasterize_annotations'))
        for bid in asset['original_block_ids']: self.record(self.by_id[bid],self.page,target)
        self.flow.append({'kind':'visual','asset_id':asset['id'],'block_ids':asset['original_block_ids'],
                          'page':self.page.number+1,'bbox':list(target)})
        self.y=target.y1+6
        for unit,text in zip(captions,captions_text):
            unit['_rendered_text']=text
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
        if dedicated:self.new_page()


def validate_output(original,pdf,units,assets,flow):
    text_count=0
    for unit in units:
        if not unit.get('placements'): raise PDFError('unplaced_source_block')
        if unit['kind']!='visual':
            fragments=[pdf[p['japanese_page']-1].get_text('text',clip=fitz.Rect(p['japanese_bbox']['rect'])) for p in unit['placements']]
            if norm(''.join(fragments))!=norm(unit.get('_rendered_text',unit.get('text',unit['block']['text']))): raise PDFError('text_missing_or_duplicate')
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
    for unit in units:
        if unit['kind']!='visual':unit['_placement_state']='validated'
    quality=[]
    for asset in assets:
        try: quality.append(visual_quality(original,pdf,asset))
        except VisualQualityError as error: quality.extend(error.quality['visuals'])
    by_asset={q['asset_id']:q for q in quality}
    for unit in units:
        if unit['kind']=='visual' and unit['asset_id'] in by_asset:
            q=by_asset[unit['asset_id']]
            unit.update(_visual_quality_state=q['status'],_visual_quality_error=q['error'],
                        _placement_state='validated' if q['status']=='passed' else 'failed')
    if any(q['status']=='failed' for q in quality): raise VisualQualityError(quality)
    source_ids=[b['id'] for u in units for b in source_members(u)]
    if len(source_ids)!=len(set(source_ids)):raise PDFError('duplicate_source_mapping')
    return {'text_blocks_validated':text_count,'missing_or_duplicate':0,'overlaps':0,
            'outside_page':0,'reading_order_preserved':True,'visuals':quality,
            'visual_quality_version':VISUAL_QUALITY_VERSION,'failed_visuals':0,'passed_visuals':len(quality),
            'source_blocks_mapped':len(source_ids),'source_mapping_duplicates':0,'untranslated_text_validated':sum(u['kind']!='visual' and u['status']!='translated' for u in units)}


def generate_pdf(original,units,output_path):
    error=next((u['content_error'] for u in units if u.get('content_error')),None)
    if error:raise PDFError(error)
    with fitz.open(stream=original,filetype='pdf') as source,fitz.open() as output:
        font=fitz.Font('cjk')
        with tempfile.TemporaryDirectory(prefix='kg-reflow-font-') as directory:
            Path(directory,'kg-cjk.ttf').write_bytes(font.buffer)
            layout=Reflow(source,output,units,fitz.Archive(directory),font)
            ordered=sorted((u for u in units if 'layout_item' in u),key=lambda u:u['layout_item']['order'])
            titles=[u for u in ordered if u.get('semantic_role')=='paper_title']
            front=[u for u in ordered if u['kind']=='metadata' and u.get('semantic_role') not in ('running_header','footer','footnote','page_decoration')]
            trailing=[u for u in ordered if u['kind']=='metadata' and u not in front]
            ordered=titles+front+[u for u in ordered if u not in titles+front+trailing]+trailing
            section=''
            labels=set()
            for index,owner in enumerate(ordered):
                item=owner['layout_item']
                if item['kind']=='text' and owner.get('semantic_role') in ('abstract_heading','section_heading') and index+1<len(ordered):
                    following=ordered[index+1]['layout_item']
                    if following['kind']=='asset':
                        asset=following['asset']; clip=fitz.Rect(asset['source_bbox'])
                        width=max(WIDTH,clip.width+MARGIN*2+1)
                        caption_chars=sum(len(layout.by_id[b].get('text') or layout.by_id[b]['block']['text']) for b in asset['caption_block_ids'])
                        reserve=clip.height+math.ceil(caption_chars/max(1,(width-MARGIN*2)/CAPTION_SIZE))*CAPTION_SIZE*1.6+110
                        if width!=layout.page.rect.width or layout.y+reserve>layout.page.rect.height-MARGIN:
                            layout.new_page(width,max(HEIGHT,reserve+MARGIN*2))
                if item['kind'] in ('text','metadata'):
                    if owner['kind']=='metadata':
                        if owner.get('semantic_role')=='page_decoration':
                            layout.paragraph(owner);continue
                        category=('footnotes' if owner.get('semantic_role')=='footnote' else 'authors' if owner.get('semantic_role') in ('authors','affiliations','contact') else
                                  'trailing' if owner in trailing else 'bibliographic')
                        if category not in labels:
                            # A native wide asset deliberately ends its page.
                            # Reuse its remaining space for text-only end matter,
                            # rather than create an almost-empty footer page.
                            if category in ('trailing','footnotes') and layout.y==MARGIN and len(output)>1:
                                output.delete_page(len(output)-1);layout.page=output[-1]
                                layout.y=max((fitz.Rect(f['bbox']).y1 for f in layout.flow if f['page']==len(output)),default=MARGIN)+24
                            layout.label({'authors':'著者・所属・連絡先（原文）','bibliographic':'書誌情報（原文）','footnotes':'脚注（原文）',
                                          'trailing':'原文の周辺情報（本文外）'}[category]);labels.add(category)
                    if owner.get('semantic_role') in ('abstract_heading','section_heading'):section=owner['block']['id']
                    owner['section']='metadata' if owner['kind']=='metadata' else section
                    layout.paragraph(owner)
                else:
                    for bid in item['asset']['original_block_ids']+item['asset']['caption_block_ids']:
                        layout.by_id[bid]['section']=section
                    layout.visual(item['asset'])
            if not any(u['status']=='translated' for u in units): raise PDFError('no_safe_translated_blocks')
            if layout.y==MARGIN and len(output)>1: output.delete_page(len(output)-1)
        for page in output:
            page.insert_font(fontname='KGJapaneseNotice',fontbuffer=font.buffer)
            page.insert_text((MARGIN,26),'日本語再組版・試作（原文の選択ページのみ収録）',fontname='KGJapaneseNotice',fontsize=8,color=(.4,.4,.4))
            page.insert_text((MARGIN,page.rect.height-18),f"knowledge_growth | {page.number+1}",fontsize=8,color=(.4,.4,.4))
        toc=[]; previous_level=0
        for unit in units:
            if unit.get('placements') and unit.get('semantic_role') in ('paper_title','abstract_heading','section_heading'):
                level=min(max(1,unit['block'].get('level') or 1),previous_level+1)
                toc.append([level,unit['text'] if unit['status']=='translated' else unit['block']['text'],unit['placements'][0]['japanese_page']])
                previous_level=level
        if toc: output.set_toc(toc)
        output.embfile_add('knowledge_growth-japanese-font-NOTICE.txt',
            (ROOT/'docs/licenses/droid-NOTICE.txt').read_bytes(),desc='Droid Sans Fallback: Apache 2.0 license and attribution')
        output.set_metadata({'title':'日本語再組版・選択ページ試作','creator':'knowledge_growth '+LAYOUT_VERSION})
        output.save(output_path,garbage=4,deflate=True)
    with fitz.open(stream=original,filetype='pdf') as source,fitz.open(output_path) as completed:
        quality=validate_output(source,completed,units,layout.assets,layout.flow)
        for unit in units:unit['_placement_verified']=True
        translated=[u for u in units if u['status']=='translated']
        reading_coverage=coverage(units)
        by_id={u['block']['id']:u for u in units}
        def source_ids(ids):return [b['id'] for bid in ids for b in source_members(by_id[bid])]
        public_assets=[]
        for a in layout.assets:
            public_asset={k:v for k,v in a.items() if not k.startswith('_')}
            for key in ('original_block_ids','caption_block_ids'):public_asset[key]=source_ids(a[key])
            public_assets.append(public_asset)
        text_blocks=sum(bool(u['block']['text'].strip()) and u['kind']!='visual' for u in units)
        return {'classification_version':CLASSIFICATION_VERSION,'layout_version':LAYOUT_VERSION,'assembly_version':ASSEMBLY_VERSION,'extraction_version':EXTRACTION_VERSION,
                'overlap_version':OVERLAP_VERSION,'content_detection':content_report(units),
                'reading_quality':reading_quality(units,pdf_generated=True,quality_validated=True),
                'visual_quality_version':VISUAL_QUALITY_VERSION,'selected_pages':sorted({u['block']['page'] for u in units}),
                'japanese_page_count':len(completed),'original_page_count':len(source),
                'counts':dict(Counter(u['status'] for u in units)),'text_blocks':text_blocks,
                'source_text_blocks':sum(bool(u['block']['text'].strip()) for u in units),
                'translated_ratio':reading_coverage['body']['ratio'] or 0,'coverage':reading_coverage,
                'translated_pages':sorted({p['japanese_page'] for u in translated for p in u['placements']}),
                'quality':quality,'assets':public_assets,'semantic_units':semantic_report(units),
                'reading_order':[bid for item in layout.flow for bid in source_ids(item['block_ids'])],
                'units':report_units(units)}
