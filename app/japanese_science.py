"""Positive scientific objects: native images, paths, tables and math structure."""
import re
import fitz
from .japanese_extraction import decorative_drawings
from .japanese_textflow import text_frame_decorations
from .japanese_boundaries import seed

from .japanese_semantics import FRONT_ROLES


def padded(rect,bounds,margin=2):
    r=fitz.Rect(rect)
    return fitz.Rect(r.x0-margin,r.y0-margin,r.x1+margin,r.y1+margin) & bounds


def scientific_objects(page,blocks,text_flow,bounds):
    raw = page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    nonbody=[padded(b['bbox']['rect'],bounds,.2) for b in blocks if b.get('bbox') and b.get('semantic_role') in FRONT_ROLES]
    lines = [line for b in raw['blocks'] if b['type']==0 for line in b['lines']
             if not any(r.contains(fitz.Rect(line['bbox'])) for r in nonbody)]
    drawings,decorations=decorative_drawings(page,blocks)
    decorations=text_frame_decorations(page,blocks,drawings,text_flow,decorations)
    seeds=[seed('image',image['bbox'],bounds,i) for i,image in enumerate(page.get_image_info())]
    # Keep the existing graphics safety margin mandatory; only optional text
    # padding can be removed by boundary separation.
    seeds += [seed('drawing',d['rect'],bounds,i,core_margin=2)
              for i,d in enumerate(drawings) if i not in decorations]
    # Ingest's figure/table bbox is a candidate, not a scientific boundary. Re-
    # derive graphics from actual objects; a legacy oversized candidate must not
    # expand a small rule to include an entire prose column.
    seeds += [seed('equation',b['bbox']['rect'],bounds,i,owner=b['id']) for i,b in enumerate(blocks)
              if b.get('bbox') and text_flow.facts.get(b['id'],{}).get('lane')=='equation']
    seeds += [seed('equation',r['rect'],bounds,len(seeds)+i) for i,r in enumerate(text_flow.math_regions)]
    warning = 'annotations_present' if any(page.annots() or []) else None
    try:
        seeds += [seed('detected_table',t.bbox,bounds,i,core_margin=2) for i,t in enumerate(page.find_tables().tables)]
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
        seeds.append(seed('numeric_table',r,bounds,len(seeds),core_margin=2))
    return seeds,drawings,decorations,warning
