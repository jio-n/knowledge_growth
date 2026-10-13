"""Plan source-order content and intact visual/caption groups, without redaction."""
from hashlib import sha256
import re
import fitz
from .japanese_translation import MAX_BLOCK_CHARS, MAX_UNITS, PDFError
from .japanese_semantics import classify_page,assemble_page,FRONT_ROLES

MATH = re.compile(r'[=∑∫√∞±×÷≤≥≠∂∇²³α-ωΑ-Ω]|[\^_{}]|\\(?:frac|sum|begin)')
CAPTIONS = ('figure_caption', 'table_caption')


def padded(rect, bounds, margin=2):
    r = fitz.Rect(rect)
    return fitz.Rect(r.x0-margin, r.y0-margin, r.x1+margin, r.y1+margin) & bounds


def merge_regions(regions):
    result = []
    for region in regions:
        r = fitz.Rect(region)
        while True:
            hits = [old for old in result if old.intersects(r)]
            if not hits: break
            for old in hits: r |= old; result.remove(old)
        result.append(r)
    return result


def plan_page(page, blocks):
    bounds = fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    units = [dict(block=b, status='pending', reason=None, kind='text', asset_id=None) for b in blocks]
    raw = page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    nonbody=[padded(b['bbox']['rect'],bounds,.2) for b in blocks if b.get('bbox') and b.get('semantic_role') in FRONT_ROLES]
    lines = [line for b in raw['blocks'] if b['type']==0 for line in b['lines']
             if not any(r.contains(fitz.Rect(line['bbox'])) for r in nonbody)]
    regions = [padded(i['bbox'],bounds) for i in page.get_image_info()]
    regions += [padded(d['rect'],bounds) for d in page.get_drawings()]
    regions += [padded(b['bbox']['rect'],bounds) for b in blocks if b.get('bbox') and b.get('role') in
                ('figure_candidate','table_candidate','equation_candidate')]
    warning = 'annotations_present' if any(page.annots() or []) else None
    try:
        regions += [padded(t.bbox,bounds) for t in page.find_tables().tables]
    except Exception:
        warning = 'table_detection_failed'
    # Unruled tables: aligned numeric rows plus their nearby header. Keep model
    # names with the numbers, including names printed in a different font size.
    rows = []
    for line in lines:
        r = fitz.Rect(line['bbox']); text = ''.join(s['text'] for s in line['spans']).strip()
        row = next((row for row in rows if abs(row[0][0].y0-r.y0)<6), None)
        if row is None: rows.append([(r,text)])
        else: row.append((r,text))
    numeric = [row for row in rows if len(row)>=2 and any(re.fullmatch(r'[+-]?\d+(?:\.\d+)?%?', t) for _,t in row)]
    for row in numeric:
        r=fitz.Rect(row[0][0])
        for box,_ in row[1:]: r |= box
        for header in rows:
            hr=fitz.Rect(header[0][0])
            for box,_ in header[1:]: hr |= box
            if len(header)>=2 and 0 <= r.y0-hr.y1 < 25 and hr.x0<r.x1 and hr.x1>r.x0: r |= hr
        regions.append(padded(r,bounds))
    # Math and genuinely overlapping text remain source display objects.
    for b in blocks:
        if not b.get('bbox'): warning = 'missing_geometry'; continue
        rect=fitz.Rect(b['bbox']['rect'])
        if not bounds.contains(rect) or rect.is_empty: warning='invalid_geometry'; continue
        if b.get('semantic_role') in FRONT_ROLES:continue
        if MATH.search(b['text']) or any(rect.intersects(fitz.Rect(line['bbox'])) and
                (tuple(line['dir'])!=(1.,0.) or any(re.search(r'math|symbol|dingbat|cmmi|cmsy|cmex',s['font'],re.I) for s in line['spans'])) for line in lines):
            regions.append(padded(rect,bounds))
        for other in blocks:
            if other['id']!=b['id'] and other.get('bbox') and b['text'] and other['text'] and rect.intersects(fitz.Rect(other['bbox']['rect'])):
                regions.append(padded(rect | fitz.Rect(other['bbox']['rect']),bounds))
    regions=merge_regions(regions)
    # Capture whole intersecting source blocks, not half a line inside a visual.
    while True:
        expanded=[]
        for region in regions:
            r=fitz.Rect(region)
            for b in blocks:
                if b.get('bbox') and b.get('role') not in CAPTIONS and r.intersects(fitz.Rect(b['bbox']['rect'])):
                    r |= padded(b['bbox']['rect'],bounds)
            expanded.append(r)
        joined=merge_regions(expanded)
        if [tuple(r) for r in joined]==[tuple(r) for r in regions]: break
        regions=joined
    captions=[u for u in units if u['block'].get('role') in CAPTIONS]
    associations={i:[] for i in range(len(regions))}
    for u in captions:
        r=fitz.Rect(u['block']['bbox']['rect']) if u['block'].get('bbox') else bounds
        candidates=[]
        for i,region in enumerate(regions):
            gap=max(region.y0-r.y1,r.y0-region.y1,0)
            if r.x0<region.x1 and r.x1>region.x0 and gap<64:
                # A caption within the source visual cannot be separated safely.
                if region.intersects(r): warning='caption_inside_visual'
                candidates.append((gap,i))
        candidates.sort()
        if not candidates: warning='caption_without_visual'
        elif len(candidates)>1 and candidates[1][0]-candidates[0][0]<10: warning='ambiguous_caption'
        else: associations[candidates[0][1]].append(u)
    if any(len(c)>1 for c in associations.values()): warning='ambiguous_caption'
    # Ingest may split a multiline caption after its labelled first line. Attach
    # adjacent, aligned continuation blocks while the preceding line is unfinished.
    for attached in associations.values():
        if len(attached)!=1: continue
        previous=attached[0]['block']
        while not previous['text'].rstrip().endswith(('.', '?', '!')):
            following=next((u for u in units if u['block']['idx']==previous['idx']+1),None)
            if not following or following['block']['kind'] not in ('para','heading') or following['block'].get('role'): break
            if abs(following['block'].get('_font_size',11)-previous.get('_font_size',11))>.5:break
            a=fitz.Rect(previous['bbox']['rect']); b=fitz.Rect(following['block']['bbox']['rect'])
            if abs(a.x0-b.x0)>8 or not 0<=b.y0-a.y1<8: break
            attached.append(following);previous=following['block']
    if warning:
        # Preserve the complete selected source page when its segmentation cannot
        # establish an unambiguous visual/caption contract. No content is lost.
        regions=[bounds]; associations={0:[]}
    assets=[]
    consumed=set()
    for i,r in enumerate(regions):
        members=[u for u in units if warning or (u['block'].get('bbox') and r.intersects(fitz.Rect(u['block']['bbox']['rect'])))]
        attached=associations[i]
        ids=[u['block']['id'] for u in members]
        aid='V'+sha256(repr((page.number,tuple(r),ids)).encode()).hexdigest()[:24]
        role=next((u['block'].get('role') for u in members if u['block'].get('role') in ('table_candidate','equation_candidate')),None)
        asset=dict(id=aid, source_page=page.number+1, source_bbox=list(r), kind='table' if role=='table_candidate' or any(u['block'].get('role')=='table_caption' for u in attached) else 'equation' if role=='equation_candidate' or (any(u['block']['text'] for u in members) and all(MATH.search(u['block']['text']) for u in members if u['block']['text'])) else 'figure',
                   original_block_ids=ids, caption_block_ids=[u['block']['id'] for u in attached],
                   rasterize_annotations=warning=='annotations_present',
                   status='unresolved' if warning or (not attached and role!='equation_candidate') else 'resolved',
                   reason=warning or ('visual_without_caption' if not attached and role!='equation_candidate' else None))
        assets.append(asset)
        for u in members:
            u.update(status='skipped_unsafe' if warning else 'skipped_protected', reason=warning or 'visual_or_equation', kind='visual', asset_id=aid)
            consumed.add(u['block']['id'])
        for u in attached:
            u.update(kind='caption',asset_id=aid); consumed.add(u['block']['id'])
    items=[]
    for asset in assets:
        member_ids=set(asset['original_block_ids']+asset['caption_block_ids'])
        items.append(dict(kind='asset',asset=asset,order=min((b['idx'] for b in blocks if b['id'] in member_ids),default=0)))
    for u in units:
        b=u['block']
        if b['id'] in consumed: continue
        if len(b['text'])>MAX_BLOCK_CHARS: u.update(status='failed',reason='block_too_long')
        if not b['text'].strip(): u.update(status='skipped_unsafe',reason='empty_block'); continue
        items.append(dict(kind='text',unit=u,order=b['idx']))
    return units,sorted(items,key=lambda x:x['order'])


def detect_units(document,blocks,pages):
    units=[]; order=0
    if sum(b['page'] in pages for b in blocks)>MAX_UNITS:raise PDFError('too_many_blocks_select_fewer_pages')
    for number in sorted(pages):
        tagged=classify_page(document[number-1],[b for b in blocks if b['page']==number])
        page_units,items=plan_page(document[number-1],tagged)
        for unit in page_units:
            b=unit['block']
            if (unit['kind']=='visual' and b.get('semantic_role') in ('paragraph','abstract_body','section_heading')
                    and b.get('_font_size',11)<=11 and len(b['text'])<80
                    and not b['text'].rstrip().endswith(('.', '?', '!'))):
                b['semantic_role']='scientific_visual'
        page_units=assemble_page(document[number-1],page_units,items)
        owners=sorted((u for u in page_units if 'layout_item' in u),key=lambda u:u['layout_item']['order'])
        for owner in owners:
            owner['layout_item']['order']=order;order+=1
        units+=page_units
    if len(units)>MAX_UNITS: raise PDFError('too_many_blocks_select_fewer_pages')
    return units
