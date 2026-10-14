"""Plan source-order content and intact visual/caption groups, without redaction."""
from hashlib import sha256
import re
import fitz
from .japanese_translation import MAX_BLOCK_CHARS, MAX_UNITS, PDFError
from .japanese_semantics import classify_page,assemble_page,FRONT_ROLES
from .japanese_textflow import TextFlow,CLASSIFICATION_VERSION
from .japanese_science import scientific_objects
from .japanese_overlap import InkProof,overlap_pairs,OVERLAP_VERSION
from .japanese_boundaries import seed,groups_for
from collections import Counter

CAPTIONS = ('figure_caption', 'table_caption')


def padded(rect, bounds, margin=2):
    r = fitz.Rect(rect)
    return fitz.Rect(r.x0-margin, r.y0-margin, r.x1+margin, r.y1+margin) & bounds


def plan_page(page, blocks):
    bounds = fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    units = [dict(block=b, status='pending', reason=None, kind='text', asset_id=None) for b in blocks]
    proof=InkProof(page)
    text_flow=TextFlow(proof,blocks)
    for unit in units:unit['content_lane']=text_flow.facts.get(unit['block']['id'],{}).get('lane','metadata')
    seeds,drawings,decorations,warning=scientific_objects(page,blocks,text_flow,bounds)
    overlaps,limit=overlap_pairs(proof,blocks)
    diagnostics={'classification_version':CLASSIFICATION_VERSION,'source_page':page.number+1,'version':OVERLAP_VERSION,'pairs':overlaps,
                 'pair_counts':dict(Counter(p['classification'] for p in overlaps)),
                 'pair_limit_reached':limit,'boundary_conflicts':[],'regions':[],
                 'layout_decoration_objects':sorted(decorations)}
    for b in blocks:
        if not b.get('bbox'):warning='missing_geometry';continue
        r=fitz.Rect(b['bbox']['rect'])
        if r.is_empty or not bounds.contains(r):warning='invalid_geometry'
    # Native crop fidelity and ordinary-text recovery have different contracts.
    # Preserve the original overlap diagnosis; record the independent proof used
    # to keep recoverable prose out of a text-only native crop.
    by_id={b['id']:b for b in blocks}
    planned=[];recoveries=[]
    for pair in overlaps:
        members=[by_id[bid] for bid in pair['block_ids']]
        recovered=(pair['classification']=='overlap_unproven'
                   and text_flow.separated(*members,seeds))
        if recovered:
            recoveries.append({'block_ids':pair['block_ids'],'classification':'verified_prose_outlines_separated',
                               'glyph_counts':pair['glyph_counts'],
                               'source_chars':sum(len(re.sub(r'\s+','',b['text'])) for b in members)})
            planned.append({**pair,'classification':'font_envelopes_only'})
        else:planned.append(pair)
    diagnostics['prose_recovery']=recoveries
    unresolved=[b for b in blocks if text_flow.facts.get(b['id'],{}).get('lane')=='unresolved_text']
    groups,boundaries=groups_for(proof,blocks,seeds,planned,bounds,CAPTIONS,unresolved=unresolved)
    diagnostics.update(boundaries)
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
        kinds={v['kind'] for v in groups[i]['seeds']} if not warning else set()
        caption_role=attached[0]['block'].get('role') if attached else None
        role=('table_candidate' if caption_role=='table_caption' else 'figure_candidate' if caption_role=='figure_caption' else
              'table_candidate' if kinds & {'detected_table','numeric_table'} else 'equation_candidate' if 'equation' in kinds else
              next((u['block'].get('role') for u in members if u['block'].get('role') in ('table_candidate','equation_candidate')),None))
        asset=dict(id=aid, source_page=page.number+1, source_bbox=list(r), kind='original_text' if not warning and groups[i]['text_only'] else 'table' if role=='table_candidate' or any(u['block'].get('role')=='table_caption' for u in attached) else 'equation' if role=='equation_candidate' else 'figure',
                   classification='unresolved_page' if warning else 'unresolved_text' if groups[i]['text_only'] else 'scientific_visual',
                   seed_ids=[] if warning else [v['id'] for v in groups[i]['seeds']],
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
            u.update(status='skipped_unsafe' if warning or groups[i]['text_only'] else 'skipped_protected',
                     reason=warning or ('text_provenance_or_overlap_unresolved' if groups[i]['text_only'] else 'visual_or_equation'),
                     kind='visual',asset_id=aid)
            u['content_lane']=asset['classification']
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
        diagnostics['text_units']=[{'block_id':u['block']['id'],'role':u['block'].get('semantic_role'),
            'source_chars':len(re.sub(r'\s+','',u['block']['text'])),
            'bbox_pt':[round(v,3) for v in u['block']['bbox']['rect']],
            'kind':u['kind'],'asset_id':u['asset_id'],'reason':u['reason'],
            'glyph_proof_error':proof.glyphs(u['block'])['error'],
            'glyph_count':proof.glyphs(u['block'])['glyph_count'],
            'math_glyph_count':proof.glyphs(u['block'])['math_glyphs'],
            'native_span_membership':u['block'].get('_extraction',{}).get('native_span_membership',False),
            'text_flow':text_flow.facts.get(u['block']['id'],{}),
            'classification':u.get('content_lane','metadata' if u['kind']=='metadata' else 'text'),
            'scientific_relations':[c for c in diagnostics['seed_contacts'] if c['block_id']==u['block']['id']],
            'positive_seed_ids':[v['id'] for v in seeds if v.get('owner_block_id')==u['block']['id']]
            } for u in units if u['block']['text'].strip() and u['block'].get('bbox')]
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
            if unit.get('content_lane')=='scientific_visual' and b.get('semantic_role')=='paragraph':
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
