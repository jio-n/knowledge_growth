"""Conservative reading units over immutable source blocks (no source writes).

The translations table stores complete assembled text under a distinct key.
Original IDs/geometry stay in the artifact report, including failed jobs. No
partial translation is ever relabelled as the translation of an assembled unit.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re
import statistics
import fitz
from .japanese_extraction import glyph_lines,recover_text,prose_text

ASSEMBLY_VERSION='semantic-1'
FRONT_ROLES=frozenset({'paper_title','authors','affiliations','contact','bibliographic','marginal_metadata','running_header','footer','footnote','page_decoration'})
METADATA_ROLES=FRONT_ROLES-{'paper_title'}
BODY_ROLES=frozenset({'abstract_body','paragraph'})
SECTION=re.compile(r'^(?:\d+(?:\.\d+)*\s+)?(?:Abstract|Introduction|Related Work|Background|Methods?|Methodology|Experiments?|Results?|Discussion|Conclusion|References|Acknowledgments?)\b',re.I)
NUMBERED_SECTION=re.compile(r'^\d{1,2}(?:\.\d+)*[.)]?\s+\S')
ABSTRACT=re.compile(r'^Abstract\s*[:.—–-]?\s*$',re.I)
COMPOUND_PREFIXES=frozenset({'zero','few','one','self','semi','cross','non','multi','pre','post','high','low','real','full','long','large','small','state','in','end'})
# Portable scientific vocabulary, not a title/model/paper exception. Unknown
# joins retain the original hyphen; we never invent a spelling from fragments.
JOINED_WORDS=frozenset('multimodal classification evaluation introduction benchmark representation transformer supervised alignment implementation information generation detection segmentation recognition learning training performance architecture experiment experiments methodology research reproducible reproducibility paragraph paragraphs semantic semantics complete document documents evidence visualization quantitative qualitative geometric mathematical dimensional probability distribution consistency inference comparison reconstruction translation understanding'.split())


def rect(block):
    return fitz.Rect(block['bbox']['rect']) if block.get('bbox') else None


def restore_line_words(text):
    """Only line-boundary hyphens; ordinary Zero-shot etc. stay intact.

    Soft hyphens are formatting. At a real line boundary, protect common
    compound prefixes and identifiers; join other split alphabetic words.
    No dictionary or paper-specific title/identifier is used.
    """
    text=text.replace('\u00ad\n','').replace('\u00ad','')
    def join(match):
        left,right=match.groups()
        combined=left+right
        return combined if left.lower() not in COMPOUND_PREFIXES and combined.lower() in JOINED_WORDS else left+'-'+right
    text=re.sub(r'([A-Za-z]{2,})-\s*\n\s*([A-Za-z0-9]{2,})',join,text)
    return re.sub(r'\s+',' ',text).strip()


def block_lines(page,block):
    r=rect(block)
    if r is None:return [],0.
    raw=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    lines=[line for b in raw['blocks'] if b['type']==0 for line in b['lines']
           if r.intersects(fitz.Rect(line['bbox']))]
    spans=[s for line in lines for s in line['spans'] if r.intersects(fitz.Rect(s['bbox'])) and s['text'].strip()]
    return lines,max((s['size'] for s in spans),default=r.height)


def classify_page(page,blocks):
    tagged=deepcopy(blocks)
    width,height=page.cropbox.width,page.cropbox.height
    glyphs=glyph_lines(page)
    for b in tagged:
        lines,size=block_lines(page,b);b['_font_size']=size
        source_lines,diagnostic=recover_text(b,glyphs)
        if source_lines is not None:b['_line_text']=source_lines
        b['_extraction']=diagnostic
        b['semantic_role']='paragraph' if b['kind']=='para' else 'section_heading' if b['kind']=='heading' else 'scientific_visual'
        r=rect(b);text=restore_line_words(b.get('_line_text',b['text']))
        if b.get('role') in ('figure_caption','table_caption'):b['semantic_role']='caption'
        elif b.get('role') in ('figure_candidate','table_candidate') or (b.get('role')=='equation_candidate' and not prose_text(text)):b['semantic_role']='scientific_visual'
        elif r and text:
            vertical=any(tuple(line['dir'])!=(1.,0.) for line in lines)
            if vertical and (r.x0<width*.12 or r.x1>width*.88):b['semantic_role']='marginal_metadata'
            elif re.search(r'\barXiv\s*:|\bdoi\s*:|\b(?:Proceedings|Conference|Workshop)\b',text,re.I):b['semantic_role']='bibliographic'
            elif (r.y0>height*.88 and re.fullmatch(r'\d{1,4}',text) and abs((r.x0+r.x1)/2-width/2)<width*.14
                  and not any(other['id']!=b['id'] and other.get('bbox') and other['text'].strip()
                              and abs(rect(other).y0-r.y0)<size and min(abs(rect(other).x1-r.x0),abs(rect(other).x0-r.x1))<size*3
                              for other in tagged)):b['semantic_role']='footer'
            elif r.y0>height*.95 and len(text)<100:b['semantic_role']='footer'
            elif r.y0>height*.86 and size<=10 and re.match(r'^\d+\s+\S',text):b['semantic_role']='footnote'
            elif page.number>0 and r.y1<height*.075 and size<=11 and len(text)<130:b['semantic_role']='running_header'
            elif ABSTRACT.fullmatch(text):b['semantic_role']='abstract_heading'
    # A raised footnote mark can be a separate extraction block. Associate it
    # with adjacent small prose, never with a page-centred folio or an equation.
    for b in tagged:
        r=rect(b)
        if not r or r.y0<=height*.86 or b['_font_size']>10:continue
        text=restore_line_words(b.get('_line_text',b['text']))
        if not re.fullmatch(r'\d{1,3}',text) or abs((r.x0+r.x1)/2-width/2)<width*.14:continue
        neighbour=next((other for other in tagged if other is not b and rect(other)
                        and 0<=rect(other).x0-r.x1<25 and abs(rect(other).y0-r.y0)<12
                        and other['_font_size']<=10 and len(re.findall(r'[A-Za-z]{2,}',other['text']))>=4),None)
        if neighbour:b['semantic_role']=neighbour['semantic_role']='footnote'
    candidates=[b for b in tagged if b['text'].strip() and b['semantic_role'] not in METADATA_ROLES|{'caption','scientific_visual'}]
    abstract=next((b for b in candidates if b['semantic_role']=='abstract_heading'),None)
    # First-page title band only. Abstract/numbered section headings are never
    # titles, even when their font is the largest on a selected body page.
    title=[]
    if page.number==0:
        front=[b for b in candidates if rect(b) and rect(b).y0<min(rect(abstract).y0 if abstract else height*.24,height*.35)
               and not SECTION.match(b['text']) and not NUMBERED_SECTION.match(b['text'])]
        if front:
            biggest=max(b['_font_size'] for b in front)
            body_sizes=[b['_font_size'] for b in tagged if b['kind']=='para' and len(b['text'])>80]
            baseline=statistics.median(body_sizes) if body_sizes else 11.
            large=sorted((b for b in front if b['_font_size']>=biggest*.78),key=lambda b:(rect(b).y0,rect(b).x0))
            if large and biggest>=baseline*1.3:
                title=[large[0]]
                for b in large[1:]:
                    if -biggest*.25<=rect(b).y0-rect(title[-1]).y1<=biggest*1.15:title.append(b)
                    else:
                        # Keep every title-sized candidate as one complete
                        # original title, but do not certify/translate a band
                        # whose spacing cannot establish a coherent title.
                        title=large
                        for candidate in title:candidate['_semantic_uncertain']=True
                        break
                for b in title:b['semantic_role']='paper_title'
    if title and abstract:
        end=max(rect(b).y1 for b in title)
        for b in tagged:
            if not b['text'].strip() or not rect(b) or b in title:continue
            if end<=rect(b).y0 and rect(b).y1<=rect(abstract).y0 and b['semantic_role'] not in METADATA_ROLES|{'scientific_visual','caption'}:
                t=b['text']
                b['semantic_role']=('contact' if re.search(r'\S+@\S+',t) else
                    'affiliations' if re.search(r'\b(?:University|Institute|Laboratory|Department|School|Research|College|Corporation)\b',t,re.I) else 'authors')
    # A section heading closes Abstract. Body blocks are not made headings merely
    # because superscripts/author fonts skewed ingest's page-wide median size.
    in_abstract=False
    normal_sizes=[b['_font_size'] for b in tagged if b['semantic_role']=='paragraph' and len(b['text'])>70]
    body_size=statistics.median(normal_sizes) if normal_sizes else 11.
    for b in sorted(tagged,key=lambda b:(rect(b).y0 if rect(b) else 0,rect(b).x0 if rect(b) else 0)):
        role=b['semantic_role']
        if role=='abstract_heading':in_abstract=True;continue
        if role in FRONT_ROLES|{'caption','scientific_visual'}:
            if role=='scientific_visual' and b['kind'] in ('figure','table'):
                # A candidate covering the Abstract must not erase its role or
                # its protected character count. Genuine separate figures close
                # the Abstract band; actual section headings always close it.
                if not any(other is not b and rect(other) and rect(b) and rect(b).intersects(rect(other))
                           and (prose_text(other.get('_line_text',other['text'])) or
                                (other.get('semantic_role')=='paragraph' and len(re.findall(r'\b[A-Za-z]{2,}\b',other.get('_line_text',other['text'])))>=4))
                           for other in tagged):in_abstract=False
            continue
        text=restore_line_words(b.get('_line_text',b['text']))
        heading=bool(SECTION.match(text) and not ABSTRACT.fullmatch(text)) or (
            b['kind']=='heading' and b['_font_size']>=body_size*1.15 and len(b['text'])<100)
        if heading:b['semantic_role']='section_heading';in_abstract=False
        else:b['semantic_role']='abstract_body' if in_abstract else 'paragraph'
    return tagged


def assembled(unit_members,role):
    originals=[b for u in unit_members for b in u.get('original_blocks',[u['block']])]
    first=unit_members[0];b=deepcopy(originals[0])
    text=restore_line_words('\n'.join(x.get('_line_text',x['text']) for x in originals))
    ids=[x['id'] for x in originals]
    key=sha256(json.dumps([ASSEMBLY_VERSION,role,ids,text],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    # Preserve singleton cache only when its text is literally unchanged.
    b['id']=ids[0] if len(ids)==1 and text==b['text'] else 'S'+key[:63]
    b['text']=text;b['semantic_role']=role
    if b.get('bbox'):
        r=rect(b)
        for original in originals[1:]:
            if original['page']==b['page'] and rect(original):r |= rect(original)
        b['bbox']['rect']=list(r);b['bbox_json']=json.dumps(b['bbox'])
    result={**first,'block':b,'original_blocks':originals,'original_block_ids':ids,
            'semantic_role':role,'assembly_version':ASSEMBLY_VERSION,'group_hash':key}
    result.pop('layout_item',None)
    if any(b.get('_semantic_uncertain') for b in originals):
        result.update(status='failed',reason='title_assembly_ambiguous',assembly_uncertain=True)
    if role in METADATA_ROLES:
        result.update(kind='metadata',status='skipped_protected',reason='metadata_preserved',asset_id=None)
    return result


def reading_items(items,width):
    """Find a gutter from reading paragraphs, excluding labels inside visuals."""
    def box(item):return fitz.Rect(item['asset']['source_bbox']) if item['kind']=='asset' else rect(item['unit']['block'])
    body=[i for i in items if i['kind']=='text' and i['unit']['semantic_role'] in BODY_ROLES and box(i)]
    edges=sorted({box(i).x0 for i in body}|{box(i).x1 for i in body})
    choices=[]
    for a,z in zip(edges,edges[1:]):
        cut=(a+z)/2
        if z-a<width*.025 or not width*.25<cut<width*.75:continue
        left=[i for i in body if box(i).x1<=a];right=[i for i in body if box(i).x0>=z]
        if len(left)>=2 and len(right)>=2:choices.append((len(left)+len(right),z-a,cut))
    if not choices:return sorted(items,key=lambda i:(box(i).y0 if box(i) else 0,box(i).x0 if box(i) else 0))
    cut=max(choices)[2];separators=[];columns=[]
    for item in items:
        r=box(item)
        # A short heading in a column must not flush the opposing column.
        (separators if r is None or r.x0<cut<r.x1 else columns).append(item)
    result=[]
    def column_order(band):return sorted(band,key=lambda i:(box(i).x0>=cut,box(i).y0,box(i).x0))
    for separator in sorted(separators,key=lambda i:box(i).y0 if box(i) else 0):
        above=[i for i in columns if box(i).y1<=(box(separator).y0 if box(separator) else 0)]
        result.extend(column_order(above));columns=[i for i in columns if i not in above];result.append(separator)
    return result+column_order(columns)


def assemble_page(page,units,items):
    """Merge only adjacent, compatible text; scientific members stay untouched."""
    originals={u['block']['id']:u for u in units}
    for u in units:
        u['semantic_role']=u['block'].get('semantic_role','paragraph')
        u['original_blocks']=[u['block']];u['original_block_ids']=[u['block']['id']]
    metadata=[u for u in units if u['kind']!='visual' and u['semantic_role'] in METADATA_ROLES]
    regular=[i for i in items if i['kind']=='asset' or i['unit'] not in metadata]
    ordered=reading_items(regular,page.cropbox.width)
    result=[];plan=[];consumed=set()
    index=0
    while index<len(ordered):
        item=ordered[index]
        if item['kind']=='asset':plan.append(item);index+=1;continue
        first=item['unit'];members=[first];role=first['semantic_role'];index+=1
        if first['block']['id'] in consumed:continue
        if role=='paper_title':
            members=[i['unit'] for i in ordered if i['kind']=='text' and i['unit']['semantic_role']=='paper_title']
        while index<len(ordered) and ordered[index]['kind']=='text':
            following=ordered[index]['unit'];a=members[-1]['block'];b=following['block']
            if role=='paper_title':break
            if following['semantic_role']!=role or role not in BODY_ROLES|{'paper_title'}:break
            ra,rb=rect(a),rect(b)
            if not ra or not rb:break
            title=role=='paper_title'
            gap=rb.y0-ra.y1;size=max(a.get('_font_size',11),b.get('_font_size',11))
            if not title and (a['text'].rstrip().endswith(('.', '?', '!', ';')) or abs(ra.x0-rb.x0)>8 or not 0<=gap<=size*.7):break
            if not title and sum(len(u['block']['text']) for u in members)+len(b['text'])>5000:break
            members.append(following);index+=1
        group=assembled(members,role);result.append(group)
        if len(group['block']['text'])>5000:group.update(status='failed',reason='block_too_long')
        for u in members:consumed.add(u['block']['id'])
        plan.append({'kind':'text','unit':group})
    # Caption continuations are already associated by the geometric planner.
    # Translate each complete caption once; keep its original IDs in reports.
    for item in plan:
        if item['kind']!='asset':continue
        asset=item['asset'];captions=[originals[x] for x in asset['caption_block_ids']]
        if captions:
            group=assembled(captions,'caption');group['kind']='caption'
            result.append(group)
            for u in captions:consumed.add(u['block']['id'])
            asset['caption_block_ids']=[group['block']['id']]
    # Metadata retains every value/ID, but is kept out of the research flow.
    for role in ('authors','affiliations','contact','bibliographic','marginal_metadata','footnote','running_header','footer','page_decoration'):
        members=sorted((u for u in metadata if u['semantic_role']==role),key=lambda u:(rect(u['block']).y0,rect(u['block']).x0))
        if role=='authors':
            names=[u for u in members if re.search(r'[A-Za-z]{2}',u['block']['text'])]
            marks=[u for u in members if u not in names];arranged=[]
            for name in names:
                arranged.append(name);r=rect(name['block'])
                adjacent=[u for u in marks if -3<=rect(u['block']).x0-r.x1<25 and abs(rect(u['block']).y0-r.y0)<15]
                arranged.extend(sorted(adjacent,key=lambda u:rect(u['block']).x0))
                marks=[u for u in marks if u not in adjacent]
            members=arranged+marks
        if members:
            group=assembled(members,role);result.append(group)
            consumed.update(u['block']['id'] for u in members)
            # Front information follows the title; running metadata goes to a
            # separate final section, never between research paragraphs.
            plan.append({'kind':'metadata','unit':group})
    for u in units:
        if u['block']['id'] not in consumed:result.append(u)
    # Place title and front metadata first regardless of ingest's column order.
    titles=[i for i in plan if i['kind']=='text' and i['unit']['semantic_role']=='paper_title']
    front=[i for i in plan if i['kind']=='metadata' and i['unit']['semantic_role'] in ('authors','affiliations','contact','bibliographic','marginal_metadata')]
    trailing=[i for i in plan if i['kind']=='metadata' and i not in front]
    main=[i for i in plan if i not in titles+front+trailing]
    plan=titles+front+main+trailing
    mapping={u['block']['id']:u for u in result}
    for order,item in enumerate(plan):
        if item['kind']=='asset':
            ids=item['asset']['original_block_ids']+item['asset']['caption_block_ids']
            owner=next((mapping[x] for x in ids if x in mapping),None)
        else:owner=item['unit']
        if owner:owner['layout_item']={**{k:v for k,v in item.items() if k!='unit'},'order':order}
    return result


def source_members(unit):return unit.get('original_blocks',[unit['block']])


def report_units(units):
    """Compatibility rows point ONLY to real source blocks, never group IDs."""
    rows=[]
    for u in units:
        for b in source_members(u):
            rows.append({'block_id':b['id'],'semantic_unit_id':u['block']['id'],
                'translation_id':u.get('translation_id'),'original_block_ids':u.get('original_block_ids',[b['id']]),
                'page':b['page'],'bbox':{k:v for k,v in b['bbox'].items() if k!='spans'} if b.get('bbox') else None,
                'section':u.get('section',''),
                'semantic_role':u.get('semantic_role'),'kind':u['kind'],'asset_id':u['asset_id'],
                'status':u['status'],'reason':u['reason'],'font_size':u.get('placed_font_size'),
                'placements':u.get('placements',[]),'untranslated':u['kind']!='visual' and u['status']!='translated'})
    return rows


def semantic_report(units):
    return [{'id':u['block']['id'],'original_block_ids':[b['id'] for b in source_members(u)],
             'role':u.get('semantic_role'),'group_hash':u.get('group_hash'),
             'assembly_version':ASSEMBLY_VERSION,'status':u['status'],'reason':u['reason'],
             'translation_id':u.get('translation_id'),'placements':u.get('placements',[]),
             'title_review':u.get('title_review'),
             'source_chars':sum(len(re.sub(r'\s+','',b['text'])) for b in source_members(u)),
             'extraction':{'inferred_spaces':sum(b.get('_extraction',{}).get('inferred_spaces',0) for b in source_members(u)),
                           'glyph_text_matched':all(b.get('_extraction',{}).get('glyph_text_matched',True) for b in source_members(u)),
                           'math_font_runs':sum(b.get('_extraction',{}).get('math_font_runs',0) for b in source_members(u))},
             'protection_reasons':sorted({reason for b in source_members(u) for reason in b.get('_protection_reasons',[])})} for u in units]


def coverage(units):
    def measure(selected):
        size=lambda u:sum(len(re.sub(r'\s+','',b['text'])) for b in source_members(u))
        converted=lambda u:u['status']=='translated' or (bool(u.get('translation_succeeded')) and bool(re.search(r'[\u3040-\u30ff\u3400-\u9fff]',u.get('text',''))))
        placed=lambda u:bool(u.get('_placement_verified'))
        total=sum(size(u) for u in selected)
        done=sum(size(u) for u in selected if converted(u))
        reasons={}
        for u in selected:
            if not converted(u):
                code=u['reason'] or 'not_translated';reasons[code]=reasons.get(code,0)+size(u)
        return {'source_chars':total,'translated_source_chars':done,'ratio':done/total if total else None,
                'candidate_source_chars':sum(size(u) for u in selected if u.get('translation_candidate')),
                'protected_source_chars':sum(size(u) for u in selected if u['kind']=='visual'),
                'placed_source_chars':sum(size(u) for u in selected if placed(u)),
                'placed_translated_source_chars':sum(size(u) for u in selected if placed(u) and u['status']=='translated'),
                'untranslated_reasons':reasons}
    body=[u for u in units if u.get('semantic_role') in BODY_ROLES]
    title=[u for u in units if u.get('semantic_role')=='paper_title']
    abstract=[u for u in body if u.get('semantic_role')=='abstract_body']
    return {'body':measure(body),'abstract':measure(abstract),'caption':measure([u for u in units if u.get('semantic_role')=='caption']),
            'title':{'units':len(title),'source_blocks':sum(len(source_members(u)) for u in title),
                     'complete':len(title)==1 and not title[0].get('assembly_uncertain'),
                     'translated':len(title)==1 and title[0]['status']=='translated',
                     'meaning_review':title[0].get('title_review',{}).get('status','not_checked') if len(title)==1 else 'not_checked'},
            'abstract_complete':bool(abstract) and all(u['status']=='translated' for u in abstract),
            'body_preserved_in_visuals':sum(u['kind']=='visual' for u in body)}
