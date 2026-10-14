"""Source text is a reading stream; scientific ownership is decided separately.

Font names and overlapping font envelopes are never semantic object evidence.
Only numeric/boolean provenance escapes this module, never source characters.
"""
import re
import fitz
from .japanese_extraction import prose_text

CLASSIFICATION_VERSION='content-lanes-1'
TEXT_ROLES=frozenset({'paper_title','abstract_heading','abstract_body','section_heading','paragraph','caption'})


def equation_structure(text):
    """Require mathematical syntax, not a math font or an ingest candidate."""
    if not re.search(r'[=∑∫√∞±×÷≤≥≠∂∇]|\\(?:frac|sum|begin)',text):return False
    words=re.findall(r'\b[A-Za-z]{2,}\b',text)
    functions={'sin','cos','tan','log','exp','max','min','arg','lim','sum','det','softmax','relu','frac','begin','end'}
    narrative=[w for w in words if w.lower() not in functions]
    return not prose_text(text) and len(narrative)<2


class TextFlow:
    def __init__(self,proof,blocks):
        self.proof=proof;self.facts={};self.math_regions=[]
        for b in blocks:
            if not b.get('bbox') or not b['text'].strip():continue
            g=proof.glyphs(b)
            role=b.get('semantic_role')
            mathematical=equation_structure(b.get('_line_text',b['text'])) and role not in ('paper_title','caption')
            verified=not g['text_error'] and bool(g['native_glyphs'])
            lane='equation' if mathematical else 'text' if role in TEXT_ROLES and verified else 'unresolved_text' if role in TEXT_ROLES else 'metadata'
            facts={'lane':lane,'glyph_verified':verified,'glyph_error':g['text_error'],
                   'glyph_count':g['glyph_count'],'math_font_glyphs':g['math_glyphs'],
                   'baselines_pt':g['baselines'],'evidence':'mathematical_syntax' if mathematical else
                   'native_glyph_correspondence' if verified else 'text_provenance_unresolved'}
            self.facts[b['id']]=facts;b['_text_flow']=facts

        # A mathematical grid has spatial meaning even without an equals sign.
        # Require math-font atoms AND repeated aligned rows/columns, never font
        # alone. Preserve the complete grid as one native equation region.
        atoms=[b for b in blocks if b['id'] in self.facts
               and self.facts[b['id']]['glyph_verified'] and self.facts[b['id']]['math_font_glyphs']
               and re.fullmatch(r'[A-Za-zα-ωΑ-Ω0-9]{1,2}',b['text'].strip())]
        rows=[]
        for b in atoms:
            y=self.facts[b['id']]['baselines_pt'][0]
            row=next((r for r in rows if abs(r[0]-y)<1),None)
            if row is None:rows.append([y,[b]])
            else:row[1].append(b)
        rows.sort(key=lambda r:r[0])
        for (ya,a),(yb,b) in zip(rows,rows[1:]):
            if not 3<yb-ya<40 or len(a)<2 or len(a)!=len(b):continue
            a=sorted(a,key=lambda v:v['bbox']['rect'][0]);b=sorted(b,key=lambda v:v['bbox']['rect'][0])
            xs=[v['bbox']['rect'][0] for v in a]
            if min(y-x for x,y in zip(xs,xs[1:]))<15:continue
            if any(abs(x['bbox']['rect'][0]-y['bbox']['rect'][0])>2 for x,y in zip(a,b)):continue
            members=a+b;r=fitz.Rect(members[0]['bbox']['rect'])
            for member in members:
                r |= fitz.Rect(member['bbox']['rect'])
                self.facts[member['id']].update(lane='equation',evidence='mathematical_grid_geometry')
            self.math_regions.append({'rect':r,'block_ids':[v['id'] for v in members]})

    def separated(self,a,b,seeds):
        return (all(self.facts.get(b['id'],{}).get('lane')=='text' for b in (a,b))
                and self.proof.text_separated(a,b,seeds))


def text_frame_decorations(page,blocks,drawings,text_flow,existing):
    """A plain enclosure of verified narrative is a text frame, not a Figure.

    No image, caption, other path, table-like cell, equation or unknown glyph may
    be enclosed. Every outline is strictly inside the frame: an overprinted rule
    cannot qualify. Omit only this layout decoration when reflowing its text.
    """
    result=set(existing)
    images=[fitz.Rect(i['bbox']) for i in page.get_image_info()]
    for i,d in enumerate(drawings):
        if i in result or d['type']!='s' or len(d['items'])!=1 or d['items'][0][0]!='re':continue
        r=fitz.Rect(d['rect']);inside=fitz.Rect(r.x0+2,r.y0+2,r.x1-2,r.y1-2)
        if inside.is_empty:continue
        if any(r.intersects(im) for im in images):continue
        if any(j!=i and r.intersects(fitz.Rect(p['rect'])+(-.5,-.5,.5,.5)) for j,p in enumerate(drawings)):continue
        # A box containing sentences may still be a diagram. Require the
        # independent section-heading/reading-flow relationship as well.
        headings=[b for b in blocks if b.get('bbox') and b.get('semantic_role') in ('abstract_heading','section_heading')]
        if not any(0<=r.y0-fitz.Rect(h['bbox']['rect']).y1<max(16,h.get('_font_size',11)*2)
                   and abs(fitz.Rect(h['bbox']['rect']).x0-r.x0)<20 for h in headings):continue
        nearby=[b for b in blocks if b.get('bbox') and b['text'].strip() and r.intersects(fitz.Rect(b['bbox']['rect']))]
        if not nearby or not any(prose_text(b.get('_line_text',b['text'])) for b in nearby):continue
        if any(text_flow.facts.get(b['id'],{}).get('lane')!='text' or b.get('semantic_role') not in ('abstract_body','paragraph') for b in nearby):continue
        if any(b.get('role') in ('figure_caption','table_caption') and b.get('bbox')
               and r.x0<fitz.Rect(b['bbox']['rect']).x1 and r.x1>fitz.Rect(b['bbox']['rect']).x0
               and abs(fitz.Rect(b['bbox']['rect']).y0-r.y1)<64 for b in blocks):continue
        outlines=[box for b in nearby for box in text_flow.proof.outlines(b)]
        if outlines and all(inside.contains(box) for box in outlines):result.add(i)
    return result
