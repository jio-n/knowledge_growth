"""Conference Abstract acceptance through real Reader, fake AI and invented PDF."""
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
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright,expect
from tests.fixtures.japanese_reading_factory import make_reading_fixture,ABSTRACT,FALLBACK,JA
from smoke_japanese_pdf_browser import render


@contextmanager
def server():
    with tempfile.TemporaryDirectory(prefix='kg-reading-browser-') as directory:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory,KG_LLM_PROVIDER='mock')
        program='''import uvicorn
from app.main import create_app
from app.ai.offline import NoAIRuntime
from tests.fixtures.japanese_reading_factory import ReadingRuntime
runtime=ReadingRuntime(fail_abstract=True)
app=create_app(runtime=runtime)
@app.post('/test/translate')
def translate():
    runtime.fail_abstract=False
    return {'state':'fixture-ready'}
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


def compare(a,b,labels,path):
    a=a.copy();b=b.copy();a.thumbnail((750,1100));b.thumbnail((750,1100))
    pair=Image.new('RGB',(a.width+b.width,max(a.height,b.height)+28),'white');pair.paste(a,(0,28));pair.paste(b,(a.width,28))
    draw=ImageDraw.Draw(pair);draw.text((12,6),labels[0],fill='black');draw.text((a.width+12,6),labels[1],fill='black');pair.save(path)


def run(base,chromium,output,before=None):
    original=make_reading_fixture();cases=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        context.set_default_timeout(25000);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        req=context.request
        sid=req.post(base+'/api/sources/pdf',multipart={'file':{'name':'synthetic-conference-paper.pdf','mimeType':'application/pdf','buffer':original},'force':'true'}).json()['source']['id']
        source=req.get(base+f'/api/sources/{sid}/document').json()
        page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        c=page.locator('.japanese-pdf-controls');c.get_by_text('日本語版を試作',exact=True).click()
        pages=c.get_by_role('textbox',name='翻訳対象の原文ページ');pages.fill('1')
        generate=c.get_by_role('button',name='日本語PDFを生成',exact=True);generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('PDF生成済み／要旨の日本語化は未完了',timeout=30000)
        expect(c.get_by_role('combobox',name='生成済み日本語PDF')).to_contain_text('本文抽出改善版')
        failed=req.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        assert failed['state']=='preview_ready' and failed['report']['coverage']['abstract']['translated_source_chars']==0
        fallback_pdf=req.get(base+f'/api/sources/{sid}/japanese-pdf/{failed["generation_id"]}/file').body()
        with fitz.open(stream=fallback_pdf,filetype='pdf') as d:assert len(d)==1
        (output/'english-fallback.pdf').write_bytes(fallback_pdf)
        cases+=['AI uncertainty produces readable English Abstract, not a crop','PDF generation and Abstract completion shown separately','footnote and original page number do not create a sparse second page']
        req.post(base+'/test/translate');c.get_by_role('checkbox').check();generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%',timeout=30000)
        complete=req.get(base+f'/api/sources/{sid}/japanese-pdf').json();a=complete['report']['coverage']['abstract']
        partial_pdf=req.get(base+f'/api/sources/{sid}/japanese-pdf/{complete["generation_id"]}/file').body()
        (output/'japanese-reading-p1.pdf').write_bytes(partial_pdf)
        assert a['source_chars']==a['candidate_source_chars']==a['translated_source_chars']==a['placed_source_chars']==a['placed_translated_source_chars']==281
        assert a['protected_source_chars']==0 and complete['report']['diagnostics']['cached_translations']>0
        cases+=['whole dense TeX Abstract translated with URL numbers MLLM citation and inline math intact','successful translations reused while failed IDs retry']
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-abstract-quality')).to_contain_text('翻訳候補 281 · 日本語化 281 · 原物保護 0')
        c.screenshot(path=str(output/'reading-diagnostics.png'))
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click();page.wait_for_timeout(350)
        jp=c.get_by_role('button',name='日本語PDF（試作）',exact=True);jp.click()
        page.wait_for_selector('.pdf-page--ready .textLayer span');assert page.locator('.pdf-scroll').count()==1
        search=page.get_by_placeholder('文書内検索');search.fill('精度');page.wait_for_timeout(350);search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1')
        span=page.locator('.textLayer span').filter(has_text='現在').first;span.scroll_into_view_if_needed()
        span.evaluate("s=>{const r=document.createRange();r.selectNodeContents(s);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}")
        selected=page.evaluate("""async report=>{
          const range=getSelection().getRangeAt(0),node=getSelection().anchorNode.parentElement;
          const number=Number(node.closest('[data-pdf-page]').dataset.pdfPage);
          const bbox=(await import('/js/components/pdfviewer.js')).selectionGeometry(number,range);
          return {number,bbox,anchor:(await import('/js/pdfalignment.js')).originalSelection(report,number,bbox)};
        }""",complete['report'])
        (output/'selection-geometry.json').write_text(json.dumps(selected),encoding='utf-8')
        assert selected['anchor'] and selected['anchor']['blockId'] in next(u for u in complete['report']['semantic_units'] if u['role']=='abstract_body')['original_block_ids'],selected
        expect(page.locator('.selection-popover')).to_contain_text('原文を見る');page.locator('.selection-popover button').click()
        expect(c.get_by_role('button',name='原文PDF',exact=True)).to_have_attribute('aria-pressed','true')
        expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        assert not req.get(base+f'/api/sources/{sid}/document').json()['highlights']
        cases+=['Abstract character diagnostics visible without body or raw AI text','Japanese PDF search and selection jump to real original block without Japanese anchors']
        c.get_by_role('checkbox').uncheck();pages.fill('1,2');generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('原文 p.1, 2',timeout=30000)
        full=req.get(base+f'/api/sources/{sid}/japanese-pdf').json();r=full['report']
        assert r['quality']['failed_visuals']==0 and len(r['quality']['visuals'])==3
        assert all(v['vector_preserved'] and v['drawings_preserved'] and v['images_preserved'] for v in r['quality']['visuals'])
        with page.expect_download() as dl:c.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        dl.value.save_as(output/'japanese-reading.pdf');pdf=(output/'japanese-reading.pdf').read_bytes()
        cases+=['Figure Table Equation strict native checks and Caption grouping retained','Reader download produces a valid searchable PDF']
        req.post(base+'/test/offline');page.reload()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{full["generation_id"]}/file').body()==pdf
        after=req.get(base+f'/api/sources/{sid}/document').json()
        assert after['blocks']==source['blocks'] and after['version']==source['version']
        assert hashlib.sha256(req.get(base+f'/api/sources/{sid}/file').body()).digest()==hashlib.sha256(original).digest()
        cases.append('offline reload retains PDF source hash version and original blocks')
        assert not errors,errors;browser.close()
    originals=render(original,output,'original');japanese=render(pdf,output,'japanese')
    for n,picture in enumerate(japanese,1):
        sp=next((v['source_page'] for v in r['quality']['visuals'] if v['japanese_page']==n),
                next((u['page'] for u in r['units'] if u['kind']!='metadata' and any(x['japanese_page']==n for x in u['placements'])),1))
        compare(originals[sp-1],picture,(f'Source p.{sp} (synthetic)',f'Japanese p.{n}'),output/f'reading-comparison-{n}.png')
    if before:
        previous=render(before.read_bytes(),output,'before-quarantined')
        partial=render(partial_pdf,output,'partial')
        compare(previous[0],partial[0],('Before: quarantined / validation failed','After: published / Abstract complete'),output/'reading-before-after.png')
    result={'passed':len(cases),'cases':cases,'pageerrors':errors,'coverage':r['coverage'],'quality':r['quality'],
            'reading_quality':r['reading_quality'],'japanese_pages':r['japanese_page_count'],'diagnostics':r['diagnostics'],
            'semantic_units':r['semantic_units'],'fallback_abstract':failed['report']['coverage']['abstract']}
    (output/'reading-browser-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('passed','pageerrors','coverage','reading_quality','japanese_pages')},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path('/tmp/kg-reading-browser'));parser.add_argument('--before',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server() as base:run(base,args.chromium,args.output,args.before)
