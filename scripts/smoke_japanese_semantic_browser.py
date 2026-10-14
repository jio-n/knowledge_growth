"""Semantic paper acceptance through Reader, synthetic data and fake AI only."""
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
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import fitz
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright,expect
from tests.fixtures.japanese_semantic_factory import make_semantic_fixture,JA,TITLE,UNTRANSLATED
from smoke_japanese_pdf_browser import render


@contextmanager
def server():
    with tempfile.TemporaryDirectory(prefix='kg-semantic-browser-') as directory:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory,KG_LLM_PROVIDER='mock')
        program='''from fastapi import Body
import uvicorn
from app.main import create_app
from app.ai.offline import NoAIRuntime
from tests.fixtures.japanese_semantic_factory import SemanticRuntime
runtime=SemanticRuntime()
app=create_app(runtime=runtime)
@app.post('/test/reopen')
def reopen():
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


def run(base,chromium,output):
    original=make_semantic_fixture();cases=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        context.set_default_timeout(25000);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        req=context.request
        sid=req.post(base+'/api/sources/pdf',multipart={'file':{'name':'synthetic-semantic-paper.pdf','mimeType':'application/pdf','buffer':original},'force':'true'}).json()['source']['id']
        doc=req.get(base+f'/api/sources/{sid}/document').json()
        page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        c=page.locator('.japanese-pdf-controls');c.get_by_text('日本語版を試作',exact=True).click()
        pages=c.get_by_role('textbox',name='翻訳対象の原文ページ');pages.fill('1')
        c.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('タイトル 全文を日本語化',timeout=30000)
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        expect(c.locator('.japanese-pdf-status')).to_contain_text('本文の日本語カバレッジ 58%')
        partial=req.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        assert partial['report']['coverage']['body']['source_chars']==343
        assert partial['report']['coverage']['body']['translated_source_chars']==200
        cases+=['UI generates selected source page 1 as semantic paper','whole title and complete Abstract reported','body coverage uses source characters, not translated block ratio']
        jp=c.get_by_role('button',name='日本語PDF（試作）',exact=True)
        original_button=c.get_by_role('button',name='原文PDF',exact=True)
        page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).fill('125')
        page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).press('Enter')
        page.get_by_role('button',name='PDFを全幅で読む',exact=True).click()
        jp.click();page.wait_for_selector('.pdf-page--ready .textLayer span')
        expect(page.locator('.reader-pane-right')).to_be_hidden()
        assert page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).input_value()=='125'
        page.screenshot(path=str(output/'reader-title-abstract.png'))
        cases.append('single controller preserves zoom and panel state')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-semantic-unit').filter(has_text='論文タイトル')).to_contain_text('構成block 3件')
        expect(c.locator('.japanese-pdf-report')).to_contain_text('本文として識別した原物保持：0件')
        c.screenshot(path=str(output/'semantic-diagnostics.png'))
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        page.wait_for_timeout(350)  # ResizeObserver settles after report collapse.
        # Selecting an assembled title returns a real source block, not S<hash>.
        title=page.locator('.textLayer span').filter(has_text='FEW-SHOT').first
        title.scroll_into_view_if_needed()
        title.evaluate("s=>{const r=document.createRange();r.selectNodeContents(s);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}")
        expect(page.locator('.selection-popover')).to_contain_text('原文を見る')
        assert page.locator('.selection-popover button').count()==1
        page.locator('.selection-popover button').click()
        expect(original_button).to_have_attribute('aria-pressed','true')
        expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        assert page.locator('.pdf-evidence-marker').locator('..').get_attribute('data-pdf-page')=='1'
        assert not req.get(base+f'/api/sources/{sid}/document').json()['highlights']
        cases+=['all source block IDs exposed in semantic diagnostics','assembled title selection jumps to original with unchanged SourceAnchor']
        pages.fill('1,2,3');c.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('原文 p.1, 2, 3',timeout=30000)
        expect(c.locator('.japanese-pdf-status')).to_contain_text('本文の日本語カバレッジ 82%')
        complete=req.get(base+f'/api/sources/{sid}/japanese-pdf').json();r=complete['report']
        assert r['coverage']['body']['source_chars']==773 and r['coverage']['body']['translated_source_chars']==630
        assert all(q['status']=='passed' and q['vector_preserved'] for q in r['quality']['visuals'])
        cases+=['two-column content reflows in left-to-right paragraph order','Figure Table Equation remain native vectors with adjacent grouped captions']
        jp.click();page.wait_for_selector('.pdf-page--ready .textLayer span')
        search=page.get_by_placeholder('文書内検索');search.fill('91.012345');page.wait_for_timeout(350);search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('/2')
        cases.append('decimal precision searchable in translated text and original table')
        with page.expect_download() as download:c.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        download.value.save_as(output/'japanese-semantic.pdf');pdf=(output/'japanese-semantic.pdf').read_bytes()
        with fitz.open(stream=pdf,filetype='pdf') as generated:
            text=''.join(p.get_text() for p in generated)
            assert ''.join(text.split()).count(''.join(JA[TITLE].split()))==1
            assert ''.join(text.split()).count(''.join(UNTRANSLATED.split()))==1
            assert '原文（未翻訳）' in text and '未解決・原物保持' not in text
        assert hashlib.sha256(req.get(base+f'/api/sources/{sid}/file').body()).digest()==hashlib.sha256(original).digest()
        req.post(base+'/test/reopen');page.reload()
        expect(jp).to_have_attribute('aria-pressed','true')
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{complete["generation_id"]}/file').body()==pdf
        cases+=['untranslated text is readable selectable flow, without diagnostic notices','download and offline reload retain derived PDF and original hash']
        assert not errors,errors;browser.close()
    originals=render(original,output,'original');japanese=render(pdf,output,'japanese')
    for n,picture in enumerate(japanese,1):
        # Section/asset provenance chooses the relevant source page, not equal
        # page numbers (the reflow PDF can have more pages).
        source_page=next((u['page'] for u in r['units'] if any(x['japanese_page']==n for x in u['placements'])),1)
        a=originals[source_page-1].copy();b=picture.copy();a.thumbnail((750,1100));b.thumbnail((750,1100))
        pair=Image.new('RGB',(a.width+b.width,max(a.height,b.height)+28),'white');pair.paste(a,(0,28));pair.paste(b,(a.width,28))
        draw=ImageDraw.Draw(pair);draw.text((12,6),f'Source p.{source_page} (synthetic)',fill='black');draw.text((a.width+12,6),f'Japanese p.{n}',fill='black')
        pair.save(output/f'comparison-{n}.png')
    report={'passed':len(cases),'cases':cases,'pageerrors':errors,'coverage_p1':partial['report']['coverage'],
            'coverage_all':r['coverage'],'japanese_pages':r['japanese_page_count'],'quality':r['quality'],
            'diagnostics':r['diagnostics'],'semantic_units':r['semantic_units']}
    (output/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(cases),'pageerrors':errors,'coverage':r['coverage'],'japanese_pages':r['japanese_page_count']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path(tempfile.gettempdir())/'kg-semantic-browser')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server() as base:run(base,args.chromium,args.output)
