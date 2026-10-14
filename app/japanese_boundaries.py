"""Provenance and bounded removal of optional crop padding; no content removal."""
from hashlib import sha256
import fitz


def pad(rect,bounds,margin=2):
    r=fitz.Rect(rect)
    return fitz.Rect(r.x0-margin,r.y0-margin,r.x1+margin,r.y1+margin) & bounds


def seed(kind,rect,bounds,index,*,owner=None,core_margin=.001):
    core=pad(rect,bounds,core_margin)
    return {'id':'G'+sha256(repr((kind,index,tuple(core),owner)).encode()).hexdigest()[:20],
            'kind':kind,'object_index':index,'core':core,'display':pad(rect,bounds,max(2,core_margin)),
            'owner_block_id':owner}


def public_seed(s):
    return {k:s[k] for k in ('id','kind','object_index','owner_block_id')} | {
        'core_bbox_pt':[round(v,4) for v in s['core']],
        'display_bbox_pt':[round(v,4) for v in s['display']]}


def groups_for(proof,blocks,seeds,overlaps,bounds,captions):
    by_id={b['id']:b for b in blocks}
    eligible=[b for b in blocks if b['text'].strip() and b.get('bbox') and b.get('role') not in captions]
    groups=[];contacts=[]
    for s in seeds:
        members=set()
        for b in eligible:
            check=proof.contact(b,s['core'])
            if check['classification']!='bbox_separated':
                contacts.append({'seed_id':s['id'],'block_id':b['id'],**check})
            # Unknown is retained conservatively, but never reported as proven
            # ink contact. It cannot authorize excluding another block later.
            if check['touches'] is not False:members.add(b['id'])
        if s['owner_block_id']:members.add(s['owner_block_id'])
        groups.append({'rect':fitz.Rect(s['display']),'seed':fitz.Rect(s['core']),
                       'members':members,'text_only':False,'seeds':[s]})
    for pair in overlaps:
        if pair['classification']=='font_envelopes_only':continue
        ids=set(pair['block_ids'])
        r=fitz.Rect(by_id[pair['block_ids'][0]]['bbox']['rect']) | fitz.Rect(by_id[pair['block_ids'][1]]['bbox']['rect'])
        s=seed('text_collision',r,bounds,len(groups))
        for bid in ids:by_id[bid].setdefault('_protection_reasons',[]).append(pair['classification'])
        groups.append({'rect':fitz.Rect(s['display']),'seed':fitz.Rect(s['core']),
                       'members':ids,'text_only':True,'seeds':[s]})
    merged=[]
    for g in groups:
        while True:
            hits=[old for old in merged if old['members'] & g['members'] or any(a['core'].intersects(b['core']) for a in old['seeds'] for b in g['seeds'])]
            if not hits:break
            for old in hits:
                g['rect'] |= old['rect'];g['seed'] |= old['seed'];g['members'] |= old['members']
                g['seeds']+=old['seeds'];g['text_only'] &= old['text_only'];merged.remove(old)
        merged.append(g)
    conflicts=[];checks=[];regions=[]
    for index,g in enumerate(merged):
        gid=f'R{index+1}'
        mandatory=fitz.Rect(g['seed'])
        for bid in g['members']:
            # Immutable whole font boxes are required, not just sampled ink.
            # .001pt only encloses the ingest bbox's .001pt rounding error.
            mandatory |= pad(by_id[bid]['bbox']['rect'],bounds,.001)
            g['rect'] |= pad(by_id[bid]['bbox']['rect'],bounds)
        before=fitz.Rect(g['rect']);hits=[]
        for b in eligible:
            if b['id'] in g['members']:continue
            check=proof.contact(b,before)
            if check['touches'] is not False:hits.append((b,check))
        # This is a choice between complete source rectangles, not an eraser.
        # No path, image or member's font box can be removed from the crop.
        trial=[(b,proof.contact(b,mandatory)) for b,_ in hits]
        shrink=bool(hits) and all(c['touches'] is False for _,c in trial)
        if shrink:g['rect']=mandatory
        for (b,old),(_,new) in zip(hits,trial):
            entry={'group_id':gid,'seed_ids':[s['id'] for s in g['seeds']],
                   'block_id':b['id'],'region_members':len(g['members']),
                   'before':old,'minimum_crop':new,
                   'resolution':'padding_trimmed' if shrink else 'unresolved',
                   'mandatory_seed_and_font_boxes_preserved':True}
            checks.append(entry)
            if not shrink:conflicts.append(entry)
        regions.append({'group_id':gid,'seed_ids':[s['id'] for s in g['seeds']],
            'seed_size_pt':[round(g['seed'].width,3),round(g['seed'].height,3)],
            'before_crop_size_pt':[round(before.width,3),round(before.height,3)],
            'crop_size_pt':[round(g['rect'].width,3),round(g['rect'].height,3)],
            'member_count':len(g['members']),'expansion_steps':1,'text_only':g['text_only'],
            'crop_adjustment':'padding_trimmed' if shrink else 'unchanged',
            'mandatory_seed_and_font_boxes_preserved':True})
    return merged,{'seeds':[public_seed(s) for g in merged for s in g['seeds']],
                   'seed_contacts':contacts,'boundary_checks':checks,
                   'boundary_conflicts':conflicts,'regions':regions}
