"""Read-only local diagnostics: no AI, source text, fonts or images in output."""
import argparse
from contextlib import closing, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def diagnose(source_id,pages,inspect_crops=False):
    # Imports and MuPDF notices are captured too; output is only allowlisted JSON.
    from app.config import db_path
    from app.routes_japanese_pdf import snapshot
    from app.japanese_content import detect_units
    from app.japanese_overlap import content_report,OVERLAP_VERSION
    from app.japanese_semantics import coverage
    from app.japanese_visual_quality import cropped_visual,text_counts,glyphs
    import fitz
    with closing(sqlite3.connect(db_path().resolve().as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        _,_,blocks,data,_=snapshot(con,source_id)
    with fitz.open(stream=data,filetype='pdf') as doc:
        if not 1<=len(pages)<=3 or len(set(pages))!=len(pages) or any(p<1 or p>len(doc) for p in pages):
            raise ValueError('invalid_pages')
        units=detect_units(doc,blocks,pages)
        crops=[]
        if inspect_crops:
            for u in units:
                item=u.get('layout_item',{})
                if item.get('kind')!='asset':continue
                asset=item['asset'];page=doc[asset['source_page']-1];page.set_rotation(0)
                clip=fitz.Rect(asset['source_bbox']);diagnostics={}
                entry={'asset_id':asset['id'],'kind':asset['kind'],'source_page':asset['source_page'],
                       'original_block_chars':sum(len(''.join(b['text'].split())) for b in blocks if b['id'] in asset['original_block_ids'])}
                try:
                    with cropped_visual(page,clip,diagnostics=diagnostics) as cropped:
                        expected=page.get_text('text',clip=clip)
                        visible=text_counts(expected,cropped[0].get_text('text'))
                        all_text=text_counts(expected,cropped[0].get_text('text',clip=fitz.INFINITE_RECT()))
                        sg,_=glyphs(page,clip);cg,_=glyphs(cropped[0],cropped[0].rect)
                        entry.update(crop_text_complete=visible['order_matches'],crop_no_hidden_text=all_text['order_matches'],
                                     glyph_codes_preserved=[g[:2] for g in sg]==[g[:2] for g in cg],
                                     source_glyph_count=len(sg),crop_glyph_count=len(cg),crop_text_diagnostics=diagnostics)
                except Exception:entry['error']='local_crop_diagnostic_unavailable'
                crops.append(entry)
    for unit in units:
        if unit['status']=='pending':unit['translation_candidate']=True
    c=coverage(units)
    return {'overlap_version':OVERLAP_VERSION,'pages':content_report(units),
            'content_errors':sorted({u['content_error'] for u in units if u.get('content_error')}),
            'abstract':c['abstract'],'crop_diagnostics':crops,
            'units':[{'original_block_ids':u.get('original_block_ids',[u['block']['id']]),
                      'source_page':u['block']['page'],'role':u.get('semantic_role'),
                      'kind':u['kind'],'source_chars':len(''.join(u['block']['text'].split())),
                      'reason':u.get('reason'),'protection_reasons':u['block'].get('_protection_reasons',[])} for u in units]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-id',required=True)
    parser.add_argument('--pages',default='1',help='Comma-separated original pages, maximum 3')
    parser.add_argument('--inspect-crops',action='store_true',help='Inspect disposable native crops in memory; never writes PDFs')
    args=parser.parse_args()
    try:
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            result=diagnose(args.source_id,[int(p.strip()) for p in args.pages.split(',')],args.inspect_crops)
    except Exception:
        # Never expose a traceback, filesystem path, PDF or DB error payload.
        print(json.dumps({'error':'local_diagnostic_unavailable'}));return 1
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':sys.exit(main())
