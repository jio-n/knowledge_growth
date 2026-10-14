"""Whitespace-only recovery from PDF glyph positions, never source DB writes."""
import re
import fitz

EXTRACTION_VERSION='glyph-spacing-1'


def glyph_lines(page):
    return [line for b in page.get_text('rawdict',flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
            if b['type']==0 for line in b['lines']]


def recover_text(block,lines):
    """Accept only the same non-whitespace characters, in the same order.

    MuPDF's word extractor and ordinary spans synthesize spaces above a larger
    gap threshold. Character origins also recover narrow, physically separated
    words in TeX fonts. Never infer a word from a dictionary or split a compact
    identifier/number/URL whose glyphs are contiguous.
    """
    if not block.get('bbox') or not block['text']:return None,{}
    r=fitz.Rect(block['bbox']['rect']);strings=[];inferred=0;fonts=set()
    for line in lines:
        chars=[]
        for span in line['spans']:
            for c in span['chars']:
                cr=fitz.Rect(c['bbox'])
                if r.contains((cr.tl+cr.br)/2):chars.append((c,span))
        if not chars:continue
        text='';previous=None
        horizontal=tuple(line['dir'])==(1.,0.)
        for c,span in chars:
            value=c['c'];fonts.add(span['font'])
            if previous and horizontal and text and not text[-1].isspace() and not value.isspace():
                prior,old_span=previous
                gap=c['bbox'][0]-prior['bbox'][2]
                size=min(span['size'],old_span['size'])
                # Same baseline only: superscripts, accents and overstrikes are
                # not word separators. Existing whitespace is kept unchanged.
                baseline=abs(c['origin'][1]-prior['origin'][1])<size*.18
                if baseline and gap>max(.65,size*.10):text+=' ';inferred+=1
            text+=value;previous=(c,span)
        strings.append(text)
    recovered='\n'.join(strings)
    same=re.sub(r'\s+','',recovered)==re.sub(r'\s+','',block['text'])
    safe={'glyph_text_matched':same,'inferred_spaces':inferred if same else 0,
          'glyph_lines':len(strings),'math_font_runs':sum(bool(re.search(r'math|symbol|cmmi|cmsy|cmex',f,re.I)) for f in fonts)}
    return (recovered if same else None),safe


def prose_text(text):
    """Several ordinary words distinguish prose from a short equation/label."""
    words=len(re.findall(r'\b[A-Za-z]{2,}\b',text))
    return (words>=7 and len(text)>=60) or (words>=4 and bool(re.match(r'^(?:The|This|An?|We|Our|In|For)\s',text.strip(),re.I)))


def decorative_drawings(page,blocks):
    """Only isolated horizontal margin rules; never grids, charts or equations."""
    drawings=page.get_drawings();decorations=[];bounds=page.cropbox
    captions=[fitz.Rect(b['bbox']['rect']) for b in blocks if b.get('bbox') and b.get('role') in ('figure_caption','table_caption')]
    images=[fitz.Rect(i['bbox']) for i in page.get_image_info()]
    for i,d in enumerate(drawings):
        items=d['items'];r=fitz.Rect(d['rect'])
        if len(items)!=1 or items[0][0]!='l' or d.get('fill') is not None:continue
        if r.height>.05 or not .1<=r.width/bounds.width<=1 or d.get('width',1)>1:continue
        if not (r.y0<bounds.height*.17 or r.y0>bounds.height*.86):continue
        area=fitz.Rect(r.x0-3,r.y0-12,r.x1+3,r.y1+12)
        if any(area.intersects(x) for x in images+captions):continue
        if any(j!=i and area.intersects(fitz.Rect(other['rect'])+(-1,-1,1,1)) for j,other in enumerate(drawings)):continue
        # A rule across actual text cannot be called decoration.
        if any(b['text'].strip() and b.get('bbox') and
               fitz.Rect(r.x0,r.y0-.6,r.x1,r.y1+.6).intersects(fitz.Rect(b['bbox']['rect'])) for b in blocks):continue
        decorations.append(i)
    return drawings,set(decorations)
