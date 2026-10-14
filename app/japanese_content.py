"""Plan source-order content and intact visual/caption groups, without redaction."""
from hashlib import sha256
import re
import fitz
from .japanese_translation import MAX_BLOCK_CHARS, MAX_UNITS, PDFError
from .japanese_semantics import classify_page,assemble_page,FRONT_ROLES
from .japanese_extraction import decorative_drawings,prose_text
from .japanese_overlap import InkProof,overlap_pairs,OVERLAP_VERSION
from collections import Counter

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
    drawings,decorations=decorative_drawings(page,blocks)
    regions = [padded(i['bbox'],bounds) for i in page.get_image_info()]
    regions += [padded(d['rect'],bounds) for i,d in enumerate(drawings) if i not in decorations]
    # Ingest's figure/table bbox is a candidate, not a scientific boundary. Re-
    # derive graphics from actual objects; a legacy oversized candidate must not
    # expand a small rule to include an entire prose column.
    regions += [padded(b['bbox']['rect'],bounds) for b in blocks if b.get('bbox') and b.get('role')=='equation_candidate' and b.get('semantic_role')=='scientific_visual']
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
    numeric_count=lambda row:sum(bool(re.fullmatch(r'[+-]?\d+(?:\.\d+)?%?',t)) for _,t in row)
    numeric_candidates=[row for row in rows if len(row)>=2 and all(len(t)<80 for _,t in row) and numeric_count(row)]
    numeric=[row for row in numeric_candidates if numeric_count(row)>=2 or any(
        other is not row and len(other)==len(row) and abs(row[0][0].y0-other[0][0].y0)<40
        and all(abs(a.x0-b.x0)<5 for (a,_),(b,_) in zip(sorted(row,key=lambda v:v[0].x0),sorted(other,key=lambda v:v[0].x0))) for other in numeric_candidates)]
    for row in numeric:
        r=fitz.Rect(row[0][0])
        for box,_ in row[1:]: r |= box
        for header in rows:
            hr=fitz.Rect(header[0][0])
            for box,_ in header[1:]: hr |= box
            if len(header)>=2 and 0 <= r.y0-hr.y1 < 25 and hr.x0<r.x1 and hr.x1>r.x0: r |= hr
        regions.append(padded(r,bounds))
    proof=InkProof(page)
    overlaps,limit=overlap_pairs(proof,blocks)
    diagnostics={'source_page':page.number+1,'version':OVERLAP_VERSION,'pairs':overlaps,
                 'pair_counts':dict(Counter(p['classification'] for p in overlaps)),
                 'pair_limit_reached':limit,'boundary_conflicts':[],'regions':[]}
    # Isolated mathematics has its own positive evidence. Inspect only glyphs
    # belonging to this block, not a neighbouring line's overlapping font box.
    for b in blocks:
        if not b.get('bbox'): warning = 'missing_geometry'; continue
        rect=fitz.Rect(b['bbox']['rect'])
        if not bounds.contains(rect) or rect.is_empty: warning='invalid_geometry'; continue
        if b.get('semantic_role') in FRONT_ROLES:continue
        if not b['text'].strip():continue
        text=b.get('_line_text',b['text'])
        glyphs=proof.glyphs(b)
        words=re.findall(r'\b[A-Za-z]{2,}\b',text)
        fragment=(len(words)>=3 and sum(map(len,words))>=18 and not glyphs['error']
                  and glyphs['math_glyphs']<=glyphs['glyph_count']*.25
                  and not re.search(r'[∑∫√∂∇]|\\(?:frac|sum|begin)',text))
        prose=(prose_text(text) or b.get('semantic_role') in ('abstract_heading','section_heading')
               or fragment)
        if not prose and (MATH.search(text) or glyphs['math_glyphs'] or glyphs['nonhorizontal_glyphs']):
            regions.append(padded(rect,bounds))
            b.setdefault('_protection_reasons',[]).append('isolated_math_or_special_font')
    regions=merge_regions(regions)
    # Grow once from a positive seed, never from newly included font envelopes.
    # Text-only collision groups merge by shared members, not rectangular contact.
    groups=[]
    by_id={b['id']:b for b in blocks}
    for region in regions:
        members={b['id'] for b in blocks if b['text'].strip() and b.get('bbox') and b.get('role') not in CAPTIONS
                 and proof.touches(b,region) is not False}
        groups.append({'rect':fitz.Rect(region),'seed':fitz.Rect(region),'members':members,'text_only':False})
    for pair in overlaps:
        if pair['classification']=='font_envelopes_only':continue
        ids=set(pair['block_ids']);r=fitz.Rect(by_id[pair['block_ids'][0]]['bbox']['rect']) | fitz.Rect(by_id[pair['block_ids'][1]]['bbox']['rect'])
        for bid in ids:by_id[bid].setdefault('_protection_reasons',[]).append(pair['classification'])
        groups.append({'rect':padded(r,bounds),'seed':padded(r,bounds),'members':ids,'text_only':True})
    merged=[]
    for group in groups:
        while True:
            hits=[old for old in merged if old['members'] & group['members']]
            if not hits:break
            for old in hits:
                group['rect'] |= old['rect'];group['seed'] |= old['seed'];group['members'] |= old['members']
                group['text_only'] &= old['text_only'];merged.remove(old)
        merged.append(group)
    groups=merged
    for group in groups:
        for bid in group['members']:group['rect'] |= padded(by_id[bid]['bbox']['rect'],bounds)
        for b in blocks:
            if b['id'] in group['members'] or not b['text'].strip() or not b.get('bbox') or b.get('role') in CAPTIONS:continue
            if proof.touches(b,group['rect']) is not False:
                diagnostics['boundary_conflicts'].append({'block_id':b['id'],'region_members':len(group['members'])})
        diagnostics['regions'].append({'seed_size_pt':[round(group['seed'].width,3),round(group['seed'].height,3)],
            'crop_size_pt':[round(group['rect'].width,3),round(group['rect'].height,3)],
            'member_count':len(group['members']),'expansion_steps':1,'text_only':group['text_only']})
    regions=[g['rect'] for g in groups]
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
    candidate_owners={}
    for u in units:
        b=u['block']
        if b['text'].strip() or not b.get('bbox'):continue
        br=fitz.Rect(b['bbox']['rect']);hits=[((br & r).get_area(),i) for i,r in enumerate(regions) if br.intersects(r)]
        if hits:candidate_owners[b['id']]=max(hits)[1]
        else:
            # Decorative rules have no lexical content. Keep their real source
            # correspondence in the report, without manufacturing a Figure.
            b['semantic_role']='page_decoration'
            u.update(kind='metadata',status='skipped_protected',reason='decorative_rule')
    for i,r in enumerate(regions):
        members=[u for u in units if warning or (u['block'].get('bbox') and
                 (candidate_owners.get(u['block']['id'])==i if not u['block']['text'].strip() else u['block']['id'] in groups[i]['members']))]
        attached=associations[i]
        ids=[u['block']['id'] for u in members]
        aid='V'+sha256(repr((page.number,tuple(r),ids)).encode()).hexdigest()[:24]
        role=next((u['block'].get('role') for u in members if u['block'].get('role') in ('table_candidate','equation_candidate')),None)
        asset=dict(id=aid, source_page=page.number+1, source_bbox=list(r), kind='original_text' if not warning and groups[i]['text_only'] else 'table' if role=='table_candidate' or any(u['block'].get('role')=='table_caption' for u in attached) else 'equation' if role=='equation_candidate' or (any(u['block']['text'] for u in members) and all(MATH.search(u['block']['text']) for u in members if u['block']['text'])) else 'figure',
                   original_block_ids=ids, caption_block_ids=[u['block']['id'] for u in attached],
                   rasterize_annotations=warning=='annotations_present',
                   status='unresolved' if warning or (not attached and role!='equation_candidate') else 'resolved',
                   reason=warning or ('visual_without_caption' if not attached and role!='equation_candidate' else None))
        assets.append(asset)
        for u in members:
            if any(r.intersects(padded(d['rect'],bounds)) for j,d in enumerate(drawings) if j not in decorations):
                u['block'].setdefault('_protection_reasons',[]).append('drawing_region')
            if any(r.intersects(fitz.Rect(image['bbox'])) for image in page.get_image_info()):
                u['block'].setdefault('_protection_reasons',[]).append('image_region')
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
    if units:
        units[0]['_page_diagnostics']=diagnostics
        if not warning and (limit or diagnostics['boundary_conflicts']):units[0]['content_error']='overlap_proof_limit' if limit else 'visual_text_boundary_unresolved'
    return units,sorted(items,key=lambda x:x['order'])


def detect_units(document,blocks,pages):
    units=[]; order=0
    if sum(b['page'] in pages for b in blocks)>MAX_UNITS:raise PDFError('too_many_blocks_select_fewer_pages')
    for number in sorted(pages):
        tagged=classify_page(document[number-1],[b for b in blocks if b['page']==number])
        page_units,items=plan_page(document[number-1],tagged)
        page_diagnostics=next((u['_page_diagnostics'] for u in page_units if '_page_diagnostics' in u),None)
        content_error=next((u['content_error'] for u in page_units if u.get('content_error')),None)
        for unit in page_units:
            b=unit['block']
            if (unit['kind']=='visual' and b.get('semantic_role')=='paragraph'
                    and b.get('_font_size',11)<=11 and len(b['text'])<80
                    and not b['text'].rstrip().endswith(('.', '?', '!'))):
                b['semantic_role']='scientific_visual'
        page_units=assemble_page(document[number-1],page_units,items)
        for unit in page_units:
            unit.pop('_page_diagnostics',None);unit.pop('content_error',None)
        if page_units and page_diagnostics:page_units[0]['_page_diagnostics']=page_diagnostics
        if page_units and content_error:page_units[0]['content_error']=content_error
        owners=sorted((u for u in page_units if 'layout_item' in u),key=lambda u:u['layout_item']['order'])
        for owner in owners:
            owner['layout_item']['order']=order;order+=1
        units+=page_units
    if len(units)>MAX_UNITS: raise PDFError('too_many_blocks_select_fewer_pages')
    return units
