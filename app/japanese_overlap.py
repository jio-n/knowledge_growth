"""Local glyph-ink proof for font-envelope overlaps. Never records paper text."""
import re
import fitz
from PIL import Image,ImageChops,ImageFilter

OVERLAP_VERSION='glyph-ink-1'
PROOF_DPI=288
MAX_PROOF_PIXELS=2_000_000
MAX_OVERLAP_PAIRS=512


def folded(text):return re.sub(r'\s+','',text)


class InkProof:
    def __init__(self,page):
        self.page=page;self.traces=page.get_texttrace();self.fonts={};self.cached={};self.resolved_fonts={}
        key=lambda n:re.sub(r'[^a-z0-9]','',re.sub(r'^[A-Z]{6}\+','',n).lower())
        for record in page.get_fonts(full=True)[:512]:
            try:
                name,ext,kind,buffer=page.parent.extract_font(record[0])
                if kind=='Type3':continue
                font=fitz.Font(fontbuffer=buffer) if buffer else fitz.Font(record[3])
                for n in (record[3],font.name):
                    k=key(n)
                    if k in self.fonts and (self.fonts[k] is None or self.fonts[k].buffer!=font.buffer):self.fonts[k]=None
                    else:self.fonts[k]=font
            except Exception:continue
        self.key=key

    def font_for(self,name):
        k=self.key(name)
        if k in self.resolved_fonts:return self.resolved_fonts[k]
        if k in self.fonts:return self.fonts[k]
        # PDF PostScript names may omit the embedded font's family suffix.
        # Require a unique source program and then verify every glyph ID and
        # advance. Ambiguous aliases cannot establish a rendering proof.
        matches=[f for n,f in self.fonts.items() if f is not None and (n.startswith(k) or k.startswith(n))]
        unique={f.buffer:f for f in matches}
        result=next(iter(unique.values())) if len(unique)==1 else None
        self.resolved_fonts[k]=result;return result

    def glyphs(self,block):
        bid=block['id']
        if bid in self.cached:return self.cached[bid]
        box=fitz.Rect(block['bbox']['rect']);selected=[];characters=[];baselines=[];math=0;vertical=0;error=None
        for trace in self.traces:
            for code,gid,origin,bbox in trace['chars']:
                r=fitz.Rect(bbox)
                if not box.contains((r.tl+r.br)/2):continue
                if code<0 or code>0x10ffff:error='unsupported_glyph';continue
                c=chr(code)
                if c.isspace():continue
                characters.append(c);baselines.append(origin[1])
                math+=bool(re.search(r'math|symbol|cmmi|cmsy|cmex',trace['font'],re.I))
                vertical+=tuple(trace['dir'])!=(1.,0.)
                font=self.font_for(trace['font'])
                if (font is None or trace['type']!=0 or trace.get('opacity',1)!=1 or tuple(trace['dir'])!=(1.,0.)
                        or font.has_glyph(code)!=gid):error='unsupported_glyph_rendering';continue
                if abs(font.glyph_advance(code)*trace['size']-r.width)>.05:error='unsupported_text_transform';continue
                selected.append((origin,c,font,trace['size']))
        if ''.join(characters)!=folded(block.get('_line_text',block['text'])):error='glyph_membership_ambiguous'
        if len(selected)>5000:error='glyph_limit'
        result={'glyphs':selected,'baselines':sorted(set(round(v,3) for v in baselines)),
                'glyph_count':len(characters),'math_glyphs':math,'nonhorizontal_glyphs':vertical,'error':error}
        self.cached[bid]=result;return result

    def mask(self,block,clip):
        evidence=self.glyphs(block)
        if evidence['error']:return None
        scale=PROOF_DPI/72
        if max(1,clip.width*scale)*max(1,clip.height*scale)>MAX_PROOF_PIXELS:return None
        try:
            with fitz.open() as doc:
                p=doc.new_page(width=self.page.cropbox.width,height=self.page.cropbox.height)
                writer=fitz.TextWriter(p.rect)
                for origin,c,font,size in evidence['glyphs']:writer.append(origin,c,font=font,fontsize=size)
                writer.write_text(p)
                pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=True,colorspace=fitz.csGRAY)
                alpha=Image.frombytes('LA',(pix.width,pix.height),pix.samples).getchannel('A')
                # Expand ink by one proof pixel: rounding/antialiasing can only
                # make the proof more conservative, never hide a thin collision.
                return alpha.point(lambda v:255 if v else 0).filter(ImageFilter.MaxFilter(3))
        except Exception:return None

    def touches(self,block,clip):
        box=fitz.Rect(block['bbox']['rect'])
        area=box & clip
        if area.is_empty:return False
        mask=self.mask(block,area)
        return None if mask is None else mask.getbbox() is not None

    def compare(self,a,b):
        ra,rb=fitz.Rect(a['bbox']['rect']),fitz.Rect(b['bbox']['rect']);area=ra & rb
        ga,gb=self.glyphs(a),self.glyphs(b)
        differences=[abs(x-y) for x in ga['baselines'] for y in gb['baselines']]
        report={'block_ids':[a['id'],b['id']],'source_page':a['page'],
                'bbox_intersection_size_pt':[round(area.width,3),round(area.height,3)],
                'minimum_baseline_difference_pt':round(min(differences),3) if differences else None,
                'glyph_counts':[ga['glyph_count'],gb['glyph_count']],
                'glyph_membership_verified':not ga['error'] and not gb['error'],
                'proof_dpi':PROOF_DPI,'classification':'overlap_unproven','overlapping_ink_pixels':None}
        left,right=self.mask(a,area),self.mask(b,area)
        if left is None or right is None or left.size!=right.size:return report
        # Check the reconstruction against the actual source display in precisely
        # the disputed area. Unrepresented skew, stroke, decoration, or another
        # glyph cannot establish an ink-separation proof.
        try:
            pix=self.page.get_pixmap(matrix=fitz.Matrix(PROOF_DPI/72,PROOF_DPI/72),clip=area,alpha=True,colorspace=fitz.csGRAY)
            actual=Image.frombytes('LA',(pix.width,pix.height),pix.samples).getchannel('A').point(lambda v:255 if v else 0).filter(ImageFilter.MaxFilter(3))
            report['source_render_verified']=actual.size==left.size and ImageChops.difference(actual,ImageChops.lighter(left,right)).getbbox() is None
        except Exception:report['source_render_verified']=False
        if not report['source_render_verified']:return report
        overlap=ImageChops.multiply(left,right)
        count=overlap.histogram()[255];report['overlapping_ink_pixels']=count
        report['classification']='visible_text_collision' if count else 'font_envelopes_only'
        report['comparison_size_px']=list(left.size)
        return report


def overlap_pairs(proof,blocks):
    records=[]
    text=[b for b in blocks if b.get('bbox') and b['text'].strip()]
    for i,a in enumerate(text):
        for b in text[i+1:]:
            if not fitz.Rect(a['bbox']['rect']).intersects(fitz.Rect(b['bbox']['rect'])):continue
            if len(records)>=MAX_OVERLAP_PAIRS:return records,True
            records.append(proof.compare(a,b))
    return records,False


def content_report(units):
    return [u['_page_diagnostics'] for u in units if '_page_diagnostics' in u]
