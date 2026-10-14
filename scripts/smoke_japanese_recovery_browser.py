"""Readability and stage diagnostics with invented TeX PDF + fake plan runtime."""
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
from tests.fixtures.japanese_recovery_factory import make_recovery_fixture
from tests.fixtures.japanese_overlap_factory import PARTS,JA,TITLE_JA
from app.japanese_pdf import norm
from smoke_japanese_pdf_browser import render
from smoke_japanese_reading_browser import compare


@contextmanager
def server():
    with tempfile.TemporaryDirectory(prefix='kg-recovery-browser-') as directory:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory,KG_LLM_PROVIDER='mock')
        program='''import uvicorn,fitz
from app.main import create_app
from app.ai.offline import NoAIRuntime
from tests.fixtures.japanese_recovery_factory import RecoveryRuntime
import app.japanese_pdf as engine
runtime=RecoveryRuntime();app=create_app(runtime=runtime)
fault={'enabled':False};quality=engine.visual_quality
def inspect(source,output,asset):
    if fault['enabled'] and asset['kind']=='table':
        r=fitz.Rect(asset['japanese_bbox']['rect'])
        output[asset['japanese_page']-1].draw_line((r.x0+2,r.y0+2),(r.x0+12,r.y0+2),width=.1)
    return quality(source,output,asset)
engine.visual_quality=inspect
@app.post('/test/fault/{enabled}')
def failure(enabled:int):
    fault['enabled']=bool(enabled)
    return {'enabled':bool(enabled)}
app.router.routes.insert(0,app.router.routes.pop())
@app.post('/test/uncertain')
def uncertain():
    runtime.fail_abstract=True
    return {'ready':True}
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
    original=make_recovery_fixture();cases=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        ctx.set_default_timeout(30000);page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        req=ctx.request
        def upload():return req.post(base+'/api/sources/pdf',multipart={'file':{'name':'synthetic-recovery.pdf','mimeType':'application/pdf','buffer':original},'force':'true'}).json()['source']['id']
        sid=upload();before_document=req.get(base+f'/api/sources/{sid}/document').json()
        page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        c=page.locator('.japanese-pdf-controls')
        c.get_by_text('日本語版を試作',exact=True).click()
        c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1,2')
        submit=c.get_by_role('button',name='日本語PDFを生成',exact=True)
        req.post(base+'/test/fault/1');submit.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('visual_content_changed')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        title=c.locator('.japanese-pdf-semantic-unit').filter(has_text='論文タイトル')
        expect(title).to_contain_text('AI翻訳 成功');expect(title).to_contain_text('配置 validated')
        expect(title).to_contain_text('原物検証 not_applicable');expect(title).to_contain_text('PDF公開 failed')
        failed=req.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        assert failed['report']['quality']['failed_visuals']==1
        assert next(q for q in failed['report']['quality']['visuals'] if q['status']=='failed')['asset_kind']=='table'
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{failed["generation_id"]}/file').status==404
        title.scroll_into_view_if_needed();c.screenshot(path=str(output/'recovery-stage-failure.png'))
        cases.append('one damaged table rejects publication while title and heading remain AI successes')
        expect(c.locator('.japanese-pdf-prose-recovery')).to_have_count(5)
        expect(c.locator('.japanese-pdf-crop-text')).to_have_count(3)
        cases.append('prose recovery and crop-stage character counts appear without source strings')
        req.post(base+'/test/fault/0');c.get_by_role('checkbox').check();submit.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        expect(c.locator('.japanese-pdf-diagnostics')).to_contain_text('保存済み訳再利用：')
        ready=req.get(base+f'/api/sources/{sid}/japanese-pdf').json();r=ready['report']
        a=r['coverage']['abstract'];assert a['source_chars']==a['candidate_source_chars']==a['translated_source_chars']==a['placed_translated_source_chars']==1150
        assert a['protected_source_chars']==0 and r['diagnostics']['cached_translations']>=8
        expect(c.locator('.japanese-pdf-abstract-quality')).to_contain_text('原文 1150 · 翻訳候補 1150 · 日本語化 1150 · 原物保護 0')
        cases.append('retry reuses successful translations and publishes all 1150 Abstract characters')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click()
        page.wait_for_selector('.pdf-page--ready .textLayer span')
        search=page.get_by_placeholder('文書内検索');search.fill('汎化');search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1');search.fill('')
        span=page.locator('.textLayer span').filter(has_text='汎化').first;span.scroll_into_view_if_needed()
        span.evaluate("s=>{const r=document.createRange();r.setStart(s.firstChild,0);r.setEnd(s.firstChild,2);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}")
        page.locator('.selection-popover button').click();expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        assert page.locator('.pdf-scroll').count()==1
        cases.append('Japanese search selection and original group jump retain original SourceAnchor and one viewer')
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click()
        with page.expect_download() as dl:c.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        dl.value.save_as(output/'japanese-recovery.pdf');pdf=(output/'japanese-recovery.pdf').read_bytes()
        with fitz.open(stream=pdf,filetype='pdf') as doc:
            text=norm(''.join(p.get_text() for p in doc));positions=[text.index(norm(JA[p])) for p in PARTS]
            assert positions==sorted(positions) and all(text.count(norm(JA[p]))==1 for p in PARTS)
            assert text.count(norm(TITLE_JA))==1
        assert all(q['status']=='passed' and q['glyphs_preserved'] and q['fonts_preserved'] and q['drawings_preserved'] and q['images_preserved'] for q in r['quality']['visuals'])
        cases.append('downloaded searchable PDF preserves reading order and strict native figure table equation quality')
        req.post(base+'/test/uncertain');english=upload();page.goto(base+'/#/read/'+english)
        page.wait_for_selector('.pdf-page--ready .textLayer span')
        c.get_by_text('日本語版を試作',exact=True).click();c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1,2');submit.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('PDF生成済み／要旨の日本語化は未完了')
        fallback=req.get(base+f'/api/sources/{english}/japanese-pdf').json()
        assert fallback['report']['coverage']['abstract']['placed_source_chars']==1150
        assert fallback['report']['coverage']['abstract']['protected_source_chars']==0
        fallback_pdf=req.get(base+f'/api/sources/{english}/japanese-pdf/{fallback["generation_id"]}/file').body()
        (output/'english-fallback.pdf').write_bytes(fallback_pdf)
        assert all(u['font_size']==11 for u in fallback['report']['units'] if u['semantic_role']=='abstract_body')
        cases.append('uncertain Abstract is selectable 11pt English text rather than a small native crop')
        req.post(base+'/test/offline');page.goto(base+'/#/read/'+sid)
        expect(c.locator('.japanese-pdf-status')).to_contain_text('要旨 100%')
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{ready["generation_id"]}/file').body()==pdf
        after=req.get(base+f'/api/sources/{sid}/document').json()
        assert before_document['blocks']==after['blocks'] and before_document['version']==after['version']
        assert req.get(base+f'/api/sources/{sid}/file').body()==original
        cases.append('offline reload retains saved PDF original bytes blocks and source version')
        assert not errors,errors;browser.close()
    sources=render(original,output,'source');japanese=render(pdf,output,'japanese')
    for n,picture in enumerate(japanese,1):compare(sources[0 if n<=2 else 1],picture,('Synthetic source',f'Japanese p.{n}'),output/f'recovery-comparison-{n}.png')
    render(fallback_pdf,output,'english-fallback')
    if before:
        previous=render(Path(before).read_bytes(),output,'before')
        compare(previous[1 if len(previous)>1 else 0],japanese[0],('Before: protected prose crop','After: Japanese text reflow'),output/'recovery-before-after.png')
    report={'passed':len(cases),'cases':cases,'pageerrors':errors,'source_hash':hashlib.sha256(original).hexdigest(),
            'coverage':r['coverage'],'quality':r['quality'],'content_detection':r['content_detection'],'diagnostics':r['diagnostics'],
            'failure':failed['report'],'untranslated':fallback['report']['coverage']['abstract']}
    (output/'recovery-browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'passed':len(cases),'pageerrors':errors,'coverage':a},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path(tempfile.gettempdir())/'kg-recovery-browser')
    parser.add_argument('--before',type=Path);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server() as base:run(base,args.chromium,args.output,args.before)
