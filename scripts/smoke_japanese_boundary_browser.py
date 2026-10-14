"""Real Reader boundary acceptance, invented PDFs and fake plan runtime only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import fitz
from playwright.sync_api import sync_playwright,expect
from tests.fixtures.japanese_boundary_factory import make_boundary_fixture,PAIRS
from tests.fixtures.japanese_overlap_factory import PARTS,JA,TITLE_JA
from app.japanese_pdf import norm
from smoke_japanese_overlap_browser import server
from smoke_japanese_pdf_browser import render
from smoke_japanese_reading_browser import compare


def run(base,chromium,output):
    expect.set_options(timeout=30000)
    original=make_boundary_fixture();cases=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        ctx.set_default_timeout(30000);page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        req=ctx.request;req.post(base+'/test/complete-title')
        def upload(data):return req.post(base+'/api/sources/pdf',multipart={'file':{'name':'synthetic-boundary-paper.pdf','mimeType':'application/pdf','buffer':data},'force':'true'}).json()['source']['id']
        sid=upload(original);before=req.get(base+f'/api/sources/{sid}/document').json()
        page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        c=page.locator('.japanese-pdf-controls');c.get_by_text('日本語版を試作',exact=True).click()
        c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1,2,3')
        generate=c.get_by_role('button',name='日本語PDFを生成',exact=True);generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        expect(c.get_by_role('combobox',name='生成済み日本語PDF')).to_contain_text('境界分離検証版')
        ready=req.get(base+f'/api/sources/{sid}/japanese-pdf').json();r=ready['report'];b=r['content_detection'][1]
        assert b['pair_counts']=={'font_envelopes_only':5} and [p['glyph_counts'] for p in b['pairs']]==[list(p) for p in PAIRS]
        assert len(b['boundary_checks'])==5 and not b['boundary_conflicts']
        assert all(c['resolution']=='padding_trimmed' for c in b['boundary_checks'])
        cases.append('five 7.782pt line pairs are separated by removing only optional crop padding')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-boundary-quality')).to_have_count(5)
        expect(c.locator('.japanese-pdf-boundary-quality').first).to_contain_text('追加余白を縮小・分離済み')
        expect(c.locator('.japanese-pdf-seed-quality').first).to_contain_text('保持必須領域')
        expect(c.locator('.japanese-pdf-abstract-quality')).to_contain_text('原文 1150 · 翻訳候補 1150 · 日本語化 1150 · 原物保護 0')
        c.locator('.japanese-pdf-boundary-quality').first.scroll_into_view_if_needed()
        c.screenshot(path=str(output/'boundary-diagnostics.png'))
        cases.append('seed kind ID extent contact type and five resolved boundaries are visible without body text')
        assert r['coverage']['abstract']['placed_translated_source_chars']==1150
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click();page.wait_for_selector('.pdf-page--ready .textLayer span')
        search=page.get_by_placeholder('文書内検索');search.fill('汎化');search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1');search.fill('')
        span=page.locator('.textLayer span').filter(has_text='汎化').first;span.scroll_into_view_if_needed()
        span.evaluate("s=>{const r=document.createRange();r.setStart(s.firstChild,0);r.setEnd(s.firstChild,2);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}")
        expect(page.locator('.selection-popover')).to_contain_text('原文を見る');page.locator('.selection-popover button').click()
        expect(c.get_by_role('button',name='原文PDF',exact=True)).to_have_attribute('aria-pressed','true')
        expect(page.locator('.pdf-evidence-marker')).to_be_visible();assert page.locator('.pdf-scroll').count()==1
        cases.append('Japanese search selection and original block jump keep a single viewer and original SourceAnchor')
        with page.expect_download() as dl:c.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        dl.value.save_as(output/'japanese-boundary.pdf');pdf=(output/'japanese-boundary.pdf').read_bytes()
        with fitz.open(stream=pdf,filetype='pdf') as d:
            text=norm(''.join(p.get_text() for p in d))
            assert all(text.count(norm(JA[part]))==1 for part in PARTS) and text.count(norm(TITLE_JA))==1
        assert len(r['quality']['visuals'])==8 and all(v['status']=='passed' and v['glyphs_preserved'] and v['fonts_preserved'] and v['drawings_preserved'] and v['images_preserved'] and v['vector_preserved'] for v in r['quality']['visuals'])
        assert {v['asset_kind'] for v in r['quality']['visuals'] if v['source_page']==3}=={'figure','table','equation'}
        cases.append('1150 Abstract characters are placed once in order and all eight native assets pass unchanged strict checks')
        c.get_by_role('checkbox').check();generate.click()
        expect(c.locator('.japanese-pdf-diagnostics')).to_contain_text('保存済み訳再利用：16件')
        second=req.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        assert second['report']['coverage']['abstract']['placed_translated_source_chars']==1150
        cases.append('retry reuses sixteen successful translations and retains readable English fallback')
        unsafe=upload(make_boundary_fixture(inseparable=True));page.goto(base+'/#/read/'+unsafe)
        page.wait_for_selector('.pdf-page--ready .textLayer span')
        c.get_by_text('日本語版を試作',exact=True).click();c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1,2,3');generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('visual_text_boundary_unresolved')
        failed=req.get(base+f'/api/sources/{unsafe}/japanese-pdf').json()
        assert failed['report']['diagnostics']['failure_stage']=='content_detection' and failed['report']['diagnostics'].get('runtime_calls',0)==0
        assert req.get(base+f'/api/sources/{unsafe}/japanese-pdf/{failed["generation_id"]}/file').status==404
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-boundary-quality').first).to_contain_text('境界未解決')
        c.screenshot(path=str(output/'boundary-rejected.png'))
        cases.append('actual unrelated ink in mandatory scientific crop is rejected before AI and never published')
        req.post(base+'/test/offline');page.goto(base+'/#/read/'+sid)
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        after=req.get(base+f'/api/sources/{sid}/document').json()
        assert after['blocks']==before['blocks'] and after['version']==before['version']
        assert req.get(base+f'/api/sources/{sid}/file').body()==original
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{ready["generation_id"]}/file').body()==pdf
        cases.append('offline reload and download retain PDF source hash blocks versions and user data')
        assert not errors,errors;browser.close()
    source_images=render(original,output,'original');jp_images=render(pdf,output,'japanese')
    for n,picture in enumerate(jp_images,1):
        source_page=1 if n==1 else next((v['source_page'] for v in r['quality']['visuals'] if v['japanese_page']==n),2)
        compare(source_images[source_page-1],picture,(f'Synthetic source p.{source_page}',f'Japanese p.{n}'),output/f'boundary-comparison-{n}.png')
    result={'passed':len(cases),'cases':cases,'pageerrors':errors,'source_hash':hashlib.sha256(original).hexdigest(),
            'coverage':r['coverage'],'quality':r['quality'],'content_detection':r['content_detection'],
            'diagnostics':r['diagnostics'],'japanese_pages':r['japanese_page_count'],
            'rejected':{'error':failed['error'],'diagnostics':failed['report']['diagnostics'],'content_detection':failed['report']['content_detection']}}
    (output/'boundary-browser-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(cases),'pageerrors':errors,'abstract':r['coverage']['abstract'],'japanese_pages':r['japanese_page_count']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path('/tmp/kg-boundary-browser'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server() as base:run(base,args.chromium,args.output)
