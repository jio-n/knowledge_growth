"""Before/after quality acceptance: generated PDF, fake AI, no private papers."""
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
import fitz
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright,expect
from tests.fixtures.japanese_visual_factory import make_visual_fixture,BODY
from tests.fixtures.pdf_factory import _text


@contextmanager
def server(baseline_ref):
    with tempfile.TemporaryDirectory(prefix='kg-visual-browser-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory,KG_LLM_PROVIDER='mock')
        # Loading the committed old engine is test-only, never a production mode.
        program='''from fastapi import Body
import types,subprocess
import uvicorn
from app.main import create_app
from app import japanese_pdf as engine,routes_japanese_pdf as routes
from tests.fixtures.japanese_visual_factory import VisualFaultRuntime
runtime=VisualFaultRuntime()
app=create_app(runtime=runtime)
new_generate=engine.generate_pdf
visual=engine.Reflow.visual
state={'mode':'after'}
before_metrics=[]
def corrupt(self,asset):
    visual(self,asset)
    if state['mode']=='damaged' and asset['kind'] in ('figure','table','equation'):
        from fitz import Rect
        r=Rect(asset['japanese_bbox']['rect'])
        self.output[asset['japanese_page']-1].draw_line((r.x0+2,r.y0+2),(r.x0+12,r.y0+2),width=.1)
engine.Reflow.visual=corrupt
'''
        if baseline_ref:
            program+=f"legacy=types.ModuleType('app._legacy_visual_engine');legacy.__package__='app'\nexec(subprocess.check_output(['git','show',{baseline_ref!r}+':app/japanese_pdf.py'],text=True),legacy.__dict__)\n"
            program+='''from app.japanese_visual_quality import inspect_visual
legacy_quality=legacy.visual_quality
def before_quality(source,output,asset):
    before_metrics.append(inspect_visual(source,output,asset))
    return legacy_quality(source,output,asset)
legacy.visual_quality=before_quality
'''
        program+='''@app.post('/test/visual-mode')
def change(mode: str=Body(embed=True)):
    state['mode']=mode
    runtime.all_translated=mode=='translated'
    routes.generate_pdf=legacy.generate_pdf if mode=='before' else new_generate
    return {'mode':mode}
app.router.routes.insert(0,app.router.routes.pop())
@app.get('/test/baseline-quality')
def baseline_quality():return before_metrics
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


def render(data,path):
    result=[]
    with fitz.open(stream=data,filetype='pdf') as doc:
        for n,page in enumerate(doc,1):
            pix=page.get_pixmap(dpi=200,alpha=False);pix.save(str(path/f'japanese-page-{n}.png'))
            result.append(Image.frombytes('RGB',(pix.width,pix.height),pix.samples))
    return result


def run(base,chromium,output,baseline):
    cases=[];errors=[];data=make_visual_fixture()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(viewport={'width':1440,'height':1100});ctx.set_default_timeout(25000)
        page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        req=ctx.request
        sid=req.post(base+'/api/sources/pdf',multipart={'file':{'name':'synthetic-embedded-font.pdf','mimeType':'application/pdf','buffer':data},'force':'true'}).json()['source']['id']
        def mode(name):assert req.post(base+'/test/visual-mode',data={'mode':name}).ok
        def status():return req.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        c=page.locator('.japanese-pdf-controls');c.get_by_text('日本語版を試作',exact=True).click()
        c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1')
        old=None
        if baseline:
            mode('before');c.get_by_role('button',name='日本語PDFを生成',exact=True).click()
            expect(c.locator('.japanese-pdf-status')).to_contain_text('visual_content_changed',timeout=30000)
            old=status();assert old['report']['diagnostics']['ai_translation_succeeded']==6
            old['baseline_asset_metrics']=req.get(base+'/test/baseline-quality').json()
            assert old['baseline_asset_metrics'][0]['asset_kind']=='original_text'
            assert old['baseline_asset_metrics'][0]['raw_mean_pixel_difference']>.2
            page.screenshot(path=str(output/'before-rejected.png'))
            cases.append('old committed engine reproduces visual_content_changed with six successful AI units')
        mode('after');c.get_by_role('checkbox').check()
        c.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('日本語再組版',timeout=30000)
        partial=status();report=partial['report'];d=report['diagnostics']
        assert d['translation_candidates']==8 and d['translation_succeeded']==d['pdf_placement_succeeded']==6
        assert d['translation_failures']=={'ai_uncertain':1,'japanese_required':1} and d['protected_units']==26
        if baseline:assert d['cached_translations']==6
        assert report['visual_quality_version']=='native-2'
        assert all(q['status']=='passed' and q['mean_pixel_difference']==0 for q in report['quality']['visuals'])
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click();page.wait_for_selector('.pdf-page--ready .textLayer span')
        page.locator('.pdf-scroll').evaluate('s=>{s.scrollTop=0}')
        page.screenshot(path=str(output/'after-partial-ready.png'))
        cases+=['partial eight-candidate PDF publishes six translations and two safe originals','preserves 26 protected units and original font/vector resources']
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-report')).to_contain_text('未翻訳本文')
        expect(c.locator('.japanese-pdf-report')).to_contain_text('平均pixel差 0')
        expect(c.locator('.japanese-pdf-report')).to_contain_text('ベクター保持')
        c.screenshot(path=str(output/'asset-quality-diagnostics.png'))
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        cases.append('per-asset kind, geometry, fidelity, text and vector diagnostics visible')
        search=page.get_by_placeholder('文書内検索');search.fill('91.012345');page.wait_for_timeout(300);search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1')
        cases.append('unchanged table precision remains searchable in derived PDF')
        with page.expect_download() as download:c.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        download.value.save_as(output/'japanese-partial.pdf');pdf=(output/'japanese-partial.pdf').read_bytes()
        page.reload();expect(c.get_by_role('button',name='日本語PDF（試作）',exact=True)).to_have_attribute('aria-pressed','true')
        cases.append('partial PDF download and reload survive')
        # Partial success is retained when actual extra geometry causes rejection.
        mode('damaged');c.get_by_text('日本語版を試作',exact=True).click()
        c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1');c.get_by_role('checkbox').check()
        c.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('visual_content_changed',timeout=30000)
        expect(c.locator('.japanese-pdf-diagnostics')).to_contain_text('原物検証失敗（Figure）：2件')
        damaged=status();assert damaged['report']['quality']['failed_visuals']==4
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-report')).to_contain_text('線・図形・画像不一致')
        page.screenshot(path=str(output/'damaged-rejected.png'))
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{damaged["generation_id"]}/file').status==404
        cases.append('actual four-asset damage rejected with all causes and no published file')
        mode('translated');c.get_by_role('checkbox').check();c.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(c.locator('.japanese-pdf-diagnostics')).to_contain_text('AI翻訳成功：2件',timeout=30000)
        fixed=status();assert fixed['report']['diagnostics']['cached_translations']==6
        assert fixed['report']['diagnostics']['runtime_calls']==1
        assert req.get(base+f'/api/sources/{sid}/file').body()==data
        cases.append('regeneration reuses six saved successes and requests only two failed IDs')
        # A page with FreeText annotations is retained once as an explicitly
        # verified 200dpi appearance; another source page supplies Japanese text.
        mode('after')
        with fitz.open(stream=data,filetype='pdf') as annotated:
            mark=annotated[0].add_freetext_annot((40,775,300,805),'Synthetic annotation 1.2345');mark.update()
            second=annotated.new_page(width=595,height=842)
            _text(second,(40,40,555,90),'Abstract',size=18,bold=True)
            _text(second,(40,140,555,195),BODY)
            annotation_pdf=annotated.tobytes()
        asid=req.post(base+'/api/sources/pdf',multipart={'file':{'name':'synthetic-annotated.pdf','mimeType':'application/pdf','buffer':annotation_pdf},'force':'true'}).json()['source']['id']
        page.goto(base+'/#/read/'+asid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        ac=page.locator('.japanese-pdf-controls');ac.get_by_text('日本語版を試作',exact=True).click()
        ac.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1,2')
        ac.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(ac.locator('.japanese-pdf-status')).to_contain_text('日本語再組版',timeout=30000)
        annotated_result=req.get(base+f'/api/sources/{asid}/japanese-pdf').json()
        assert any(q.get('annotations_present') and q['annotation_raster_verified'] and not q['vector_preserved'] for q in annotated_result['report']['quality']['visuals'])
        ac.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(ac.locator('.japanese-pdf-report')).to_contain_text('画像化（200dpi・内容照合済み）')
        ac.screenshot(path=str(output/'annotation-diagnostics.png'))
        cases.append('annotation fallback verifies real pixels and explicitly declares loss of vector appearance')
        assert not errors,errors;browser.close()
    pictures=render(pdf,output)
    with fitz.open(stream=data,filetype='pdf') as doc:
        src=doc[0].get_pixmap(dpi=200,alpha=False);src.save(str(output/'original-page-1.png'))
        original=Image.frombytes('RGB',(src.width,src.height),src.samples)
    for n,picture in enumerate(pictures,1):
        a=original.copy();b=picture.copy();a.thumbnail((750,1100));b.thumbnail((750,1100))
        pair=Image.new('RGB',(a.width+b.width,max(a.height,b.height)+28),'white');pair.paste(a,(0,28));pair.paste(b,(a.width,28))
        draw=ImageDraw.Draw(pair);draw.text((12,6),'Source p.1 (synthetic)',fill='black');draw.text((a.width+12,6),f'Japanese p.{n}',fill='black')
        pair.save(output/f'comparison-{n}.png')
    result={'passed':len(cases),'cases':cases,'pageerrors':errors,'before':old,'after':partial,'damaged':damaged,'recovered':fixed}
    (output/'browser-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(cases),'cases':cases,'pageerrors':errors,'diagnostics':d,'japanese_pages':report['japanese_page_count']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path(tempfile.gettempdir())/'kg-visual-browser')
    parser.add_argument('--baseline-ref',help='Optional committed old engine, e.g. 9e1993e')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server(args.baseline_ref) as base:run(base,args.chromium,args.output,bool(args.baseline_ref))
