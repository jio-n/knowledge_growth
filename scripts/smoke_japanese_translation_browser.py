"""Safe UI diagnostics / partial success acceptance, synthetic PDF + fake runtime."""
import argparse
from contextlib import contextmanager
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
from playwright.sync_api import sync_playwright, expect
import fitz
from tests.fixtures.japanese_pdf_factory import make_translation_diagnostic_fixture as synthetic_pdf


@contextmanager
def server():
    with tempfile.TemporaryDirectory(prefix='kg-translation-browser-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory,KG_LLM_PROVIDER='mock')
        program='''from fastapi import Body
from app.main import create_app
from tests.fixtures.japanese_translation_cases import TranslationFaultRuntime
import uvicorn
runtime=TranslationFaultRuntime('bad_json')
app=create_app(runtime=runtime)
@app.post('/test/translation-mode')
def set_mode(mode: str=Body(embed=True)):
    runtime.mode=mode
    return {'mode':mode}
# Test-only route must precede the client StaticFiles catch-all.
app.router.routes.insert(0,app.router.routes.pop())
'''+f"uvicorn.run(app,host='127.0.0.1',port={port},log_level='error')"
        with open(Path(directory)/'server.log','w+') as log:
            proc=subprocess.Popen([sys.executable,'-c',program],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+20
                while True:
                    if proc.poll() is not None: raise RuntimeError('fixture_server_failed')
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.2): break
                    except OSError:
                        if time.monotonic()>deadline: raise RuntimeError('fixture_server_timeout')
                        time.sleep(.1)
                yield f'http://127.0.0.1:{port}'
            finally:
                proc.terminate();proc.wait(timeout=10)


def run(base,chromium,output):
    cases=[];errors=[];reports={}
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1100})
        context.set_default_timeout(20000)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        request=context.request
        def mode(name): assert request.post(base+'/test/translation-mode',data={'mode':name}).ok
        def upload(name):
            with fitz.open(stream=synthetic_pdf(),filetype='pdf') as doc:
                doc.set_metadata({'subject':'synthetic '+name});data=doc.tobytes()
            result=request.post(base+'/api/sources/pdf',multipart={'file':{'name':'generated.pdf','mimeType':'application/pdf','buffer':data},'force':'true'})
            assert result.ok,result.text();return result.json()['source']['id']
        def open_reader(sid):
            page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
            controls=page.locator('.japanese-pdf-controls')
            controls.get_by_text('日本語版を試作',exact=True).click()
            controls.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1')
            return controls
        def generate(controls,sid,expected):
            controls.get_by_role('button',name='日本語PDFを生成',exact=True).click()
            expect(controls.locator('.japanese-pdf-diagnostics')).to_contain_text(expected,timeout=30000)
            status=request.get(base+f'/api/sources/{sid}/japanese-pdf').json()
            reports[status['generation_id']]=status['report']['diagnostics']
            text=controls.inner_text()
            assert 'SECRET_RESPONSE' not in text and 'SECRET_CREDENTIAL' not in text
            return status
        sid=upload('json');controls=open_reader(sid)
        status=generate(controls,sid,'JSON形式不正（invalid_json）：3件')
        expect(controls.locator('.japanese-pdf-diagnostics')).to_contain_text('失敗段階：AI翻訳・応答検証')
        expect(controls.locator('.japanese-pdf-diagnostics')).to_contain_text('PDF配置成功：0件')
        expect(controls.get_by_role('button',name='日本語PDF（試作）',exact=True)).to_be_disabled()
        expect(page.locator('.pdf-scroll')).to_be_visible()
        page.screenshot(path=str(output/'failed-json-diagnostics.png'))
        cases+=['all-batch invalid JSON classified without source/raw response','original reader survives translation failure']
        mode('numbers')
        controls.get_by_role('checkbox').check()
        partial=generate(controls,sid,'数値検証失敗（numbers_changed）：1件')
        d=controls.locator('.japanese-pdf-diagnostics')
        expect(d).to_contain_text('翻訳候補：3件');expect(d).to_contain_text('AI翻訳成功：2件')
        expect(d).to_contain_text('PDF配置成功：2件');expect(d).to_contain_text('図表・数式保護：3件')
        controls.get_by_role('button',name='日本語PDF（試作）',exact=True).click()
        page.wait_for_selector('.pdf-page--ready .textLayer span')
        page.locator('.pdf-scroll').evaluate('s=>{s.scrollTop=0}')
        page.wait_for_timeout(200)
        page.screenshot(path=str(output/'partial-success-diagnostics.png'))
        cases+=['partial success retains two valid paragraphs','numeric failure isolated and layout count visible']
        with page.expect_download() as download:
            controls.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        download.value.save_as(output/'partial-japanese.pdf')
        assert (output/'partial-japanese.pdf').read_bytes().startswith(b'%PDF-')
        page.reload();expect(page.locator('.japanese-pdf-diagnostics')).to_contain_text('数値検証失敗（numbers_changed）：1件')
        cases.append('partial PDF and diagnostics restored after reload and downloadable')
        controls=page.locator('.japanese-pdf-controls')
        controls.get_by_text('日本語版を試作',exact=True).click()
        controls.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1')
        controls.get_by_role('checkbox').check();mode('valid')
        recovered=generate(controls,sid,'保存済み訳再利用：2件')
        expect(controls.locator('.japanese-pdf-diagnostics')).to_contain_text('AI翻訳成功：1件')
        expect(controls.locator('.japanese-pdf-diagnostics')).to_contain_text('PDF配置成功：3件')
        assert recovered['report']['diagnostics']['runtime_calls']==1
        cases.append('retry reuses successes and translates only failed block')
        for name,expected in [('uncertain','AI uncertain応答（ai_uncertain）：1件'),('timeout','AI timeout（ai_timeout）：3件')]:
            mode(name);sid2=upload(name);c=open_reader(sid2);generate(c,sid2,expected)
            page.screenshot(path=str(output/f'{name}-diagnostics.png'))
            cases.append(name+' safe diagnostic code and count')
        assert not errors,errors
        browser.close()
    result={'passed':len(cases),'cases':cases,'pageerrors':errors,'diagnostics':reports}
    (output/'browser-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='diagnostics'},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path(tempfile.gettempdir())/'kg-translation-browser')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server() as base: run(base,args.chromium,args.output)
