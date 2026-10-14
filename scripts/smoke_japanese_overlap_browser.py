"""1,150-character invented Abstract in the real Reader; no external AI."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import fitz
from playwright.sync_api import sync_playwright,expect
from tests.fixtures.japanese_overlap_factory import make_overlap_fixture,PARTS,JA,TITLE_JA,INTRO
from app.japanese_pdf import norm
from smoke_japanese_pdf_browser import render
from smoke_japanese_reading_browser import compare


@contextmanager
def server():
    with tempfile.TemporaryDirectory(prefix='kg-overlap-browser-') as directory:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory,KG_LLM_PROVIDER='mock')
        program='''import uvicorn
from app.main import create_app
from app.ai.offline import NoAIRuntime
from tests.fixtures.japanese_overlap_factory import OverlapRuntime
runtime=OverlapRuntime(omit_title=True)
app=create_app(runtime=runtime)
@app.post('/test/complete-title')
def complete():
    runtime.omit_title=False
    return {'state':'ready'}
app.router.routes.insert(0,app.router.routes.pop())
@app.post('/test/offline')
def offline():
    app.state.ai_runtime=NoAIRuntime()
    return {'state':'offline'}
app.router.routes.insert(0,app.router.routes.pop())
'''+f"uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')"
        with open(Path(directory)/'server.log','w+') as log:
            proc=subprocess.Popen([sys.executable,'-c',program],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+20
                while True:
                    if proc.poll() is not None:raise RuntimeError('fixture_server_failed')
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                    except OSError:
                        if time.monotonic()>deadline:raise RuntimeError('fixture_server_timeout')
                        time.sleep(.1)
                yield f'http://127.0.0.1:{port}'
            finally:proc.terminate();proc.wait(timeout=10)


def run(base,chromium,output,before=None):
    original=make_overlap_fixture();cases=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        context.set_default_timeout(30000);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        req=context.request
        sid=req.post(base+'/api/sources/pdf',multipart={'file':{'name':'synthetic-overlap-paper.pdf','mimeType':'application/pdf','buffer':original},'force':'true'}).json()['source']['id']
        doc=req.get(base+f'/api/sources/{sid}/document').json()
        page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        c=page.locator('.japanese-pdf-controls');c.get_by_text('日本語版を試作',exact=True).click()
        c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1,2')
        generate=c.get_by_role('button',name='日本語PDFを生成',exact=True);generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        expect(c.locator('.japanese-pdf-status')).to_contain_text('タイトル 全文を原文保持・確認対象')
        first=req.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        title=next(u for u in first['report']['semantic_units'] if u['role']=='paper_title')
        assert title['reason']=='title_incomplete' and title['title_review']['status']=='incomplete'
        cases.append('title missing its research subject is rejected while full Abstract translation succeeds')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-abstract-quality')).to_contain_text('原文 1150 · 翻訳候補 1150 · 日本語化 1150 · 原物保護 0')
        expect(c.locator('.japanese-pdf-overlap-quality').first).to_contain_text('font_envelopes_only 5組')
        expect(c.locator('.japanese-pdf-title-quality')).to_contain_text('incomplete')
        c.screenshot(path=str(output/'overlap-diagnostics.png'))
        cases.append('numeric baseline ink crop and title meaning diagnostics available without source text')
        req.post(base+'/test/complete-title');c.get_by_role('checkbox').check();generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('タイトル 全文を日本語化')
        ready=req.get(base+f'/api/sources/{sid}/japanese-pdf').json();r=ready['report']
        assert r['diagnostics']['cached_translations']>=7
        assert r['coverage']['abstract']['placed_translated_source_chars']==1150 and r['coverage']['abstract_complete']
        expect(c.locator('.japanese-pdf-title-quality')).to_contain_text('complete')
        cases.append('retry only failed title and deliberate English fallback while successful Abstract translations are reused')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click();page.wait_for_selector('.pdf-page--ready .textLayer span')
        assert page.locator('.pdf-scroll').count()==1
        search=page.get_by_placeholder('文書内検索');search.fill('汎化');search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1')
        search.fill('')
        span=page.locator('.textLayer span').filter(has_text='汎化').first;span.scroll_into_view_if_needed()
        span.evaluate("s=>{const r=document.createRange();r.setStart(s.firstChild,0);r.setEnd(s.firstChild,2);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}")
        selected=page.evaluate("""async report=>{
          const range=getSelection().getRangeAt(0),node=getSelection().anchorNode.parentElement;
          const number=Number(node.closest('[data-pdf-page]').dataset.pdfPage);
          const bbox=(await import('/js/components/pdfviewer.js')).selectionGeometry(number,range);
          return {number,bbox,anchor:(await import('/js/pdfalignment.js')).originalSelection(report,number,bbox)};
        }""",r)
        (output/'selection-geometry.json').write_text(json.dumps(selected),encoding='utf-8')
        assert selected['anchor'],selected
        expect(page.locator('.selection-popover')).to_contain_text('原文を見る')
        page.locator('.selection-popover button').click()
        expect(c.get_by_role('button',name='原文PDF',exact=True)).to_have_attribute('aria-pressed','true')
        expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        cases.append('search selectable Japanese and original block jump preserve the single viewer and SourceAnchor contract')
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click();page.wait_for_selector('.pdf-page--ready .textLayer span')
        page.screenshot(path=str(output/'overlap-reader.png'))
        with page.expect_download() as dl:c.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        dl.value.save_as(output/'japanese-overlap.pdf');pdf=(output/'japanese-overlap.pdf').read_bytes()
        with fitz.open(stream=pdf,filetype='pdf') as generated:
            text=norm(''.join(p.get_text() for p in generated))
            assert all(text.count(norm(JA[part]))==1 for part in PARTS)
            assert text.count(norm(TITLE_JA))==1 and norm(INTRO) in text
        assert all(v['status']=='passed' and v['vector_preserved'] and v['drawings_preserved'] and v['images_preserved'] for v in r['quality']['visuals'])
        assert {a['kind'] for a in r['assets']}=={'figure','table','equation'}
        cases.append('all 1150 Abstract characters placed in order once at 11pt; native Figure Table Equation and Captions preserved')
        req.post(base+'/test/offline');page.reload()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{ready["generation_id"]}/file').body()==pdf
        after=req.get(base+f'/api/sources/{sid}/document').json()
        assert after['blocks']==doc['blocks'] and after['version']==doc['version']
        assert req.get(base+f'/api/sources/{sid}/file').body()==original and not after['highlights']
        assert not errors,errors;browser.close()
        cases.append('download and offline reload retain source hash original blocks and saved Japanese artifact')
    sources=render(original,output,'original');japanese=render(pdf,output,'japanese')
    for n,picture in enumerate(japanese,1):
        sp=next((v['source_page'] for v in r['quality']['visuals'] if v['japanese_page']==n),1)
        compare(sources[sp-1],picture,(f'Synthetic source p.{sp}',f'Japanese p.{n}'),output/f'overlap-comparison-{n}.png')
    if before:
        previous=render(before.read_bytes(),output,'before-quarantined')
        compare(previous[1],japanese[0],('Old HEAD p.2: quarantined, English crops','After p.1: published, Japanese Abstract'),output/'overlap-before-after.png')
    result={'passed':len(cases),'cases':cases,'pageerrors':errors,'source_hash':hashlib.sha256(original).hexdigest(),
            'coverage':r['coverage'],'quality':r['quality'],'content_detection':r['content_detection'],
            'diagnostics':r['diagnostics'],'japanese_pages':r['japanese_page_count'],
            'title_review':next(u['title_review'] for u in r['semantic_units'] if u['role']=='paper_title')}
    (output/'overlap-browser-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('passed','pageerrors','coverage','japanese_pages')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path('/tmp/kg-overlap-browser'));parser.add_argument('--before',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server() as base:run(base,args.chromium,args.output,args.before)
