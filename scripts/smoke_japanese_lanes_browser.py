"""Two-path reading acceptance and selected-unit retry, invented paper only."""
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
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import fitz
from playwright.sync_api import sync_playwright,expect
from tests.fixtures.japanese_lanes_factory import make_lanes_fixture,PARTS,TRANSLATIONS
from app.japanese_pdf import norm
from smoke_japanese_pdf_browser import render
from smoke_japanese_reading_browser import compare


@contextmanager
def server():
    with tempfile.TemporaryDirectory(prefix='kg-lanes-browser-') as directory:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory,KG_LLM_PROVIDER='mock')
        program='''import uvicorn,fitz
from app.main import create_app
from app.ai.offline import NoAIRuntime
from tests.fixtures.japanese_lanes_factory import LanesRuntime
import app.japanese_pdf as engine
runtime=LanesRuntime(mixed=True);app=create_app(runtime=runtime);fault={'damage':False};quality=engine.visual_quality
def check(source,output,asset):
    if fault['damage'] and asset['kind']=='table':
        r=fitz.Rect(asset['japanese_bbox']['rect'])
        output[asset['japanese_page']-1].draw_line((r.x0+2,r.y0+2),(r.x0+12,r.y0+2),width=.1)
    return quality(source,output,asset)
engine.visual_quality=check
@app.post('/test/mode/{mode}')
def mode(mode:str):
    if mode=='complete':runtime.failures.clear()
    if mode=='damage':fault['damage']=True
    if mode=='restore':fault['damage']=False
    if mode=='offline':app.state.ai_runtime=NoAIRuntime()
    return {'ok':True}
app.router.routes.insert(0,app.router.routes.pop())
@app.get('/test/calls')
def calls():return {'count':len(runtime.inputs),'ids':[b['block_id'] for b in runtime.inputs]}
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


def run(base,chromium,out,before=None):
    expect.set_options(timeout=45000)
    original=make_lanes_fixture(frame=True);cases=[];errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        ctx.set_default_timeout(45000);page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)));req=ctx.request
        sid=req.post(base+'/api/sources/pdf',multipart={'file':{'name':'invented-dense-tex.pdf','mimeType':'application/pdf','buffer':original},'force':'true'}).json()['source']['id']
        source_document=req.get(base+f'/api/sources/{sid}/document').json()
        def job():return req.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        page.goto(base+'/#/read/'+sid);page.wait_for_selector('.pdf-page--ready .textLayer span')
        c=page.locator('.japanese-pdf-controls');c.get_by_text('日本語版を試作',exact=True).click()
        c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1,2')
        generate=c.get_by_role('button',name='日本語PDFを生成',exact=True);generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('読書品質未達')
        mixed=job();r=mixed['report'];a=r['coverage']['abstract']
        assert a['source_chars']==a['candidate_source_chars']==a['placed_source_chars']==1150 and a['protected_source_chars']==0
        assert r['diagnostics']['translation_failures']=={'ai_uncertain':2,'japanese_required':1}
        assert r['reading_quality']['quality_validated'] and not r['reading_quality']['accepted']
        expect(c.locator('.japanese-pdf-diagnostics')).to_contain_text('PDF品質検証：成功')
        expect(c.get_by_role('combobox',name='生成済み日本語PDF')).to_contain_text('本文・原物経路分離版')
        cases.append('publication and native quality success do not pass the independent Abstract reading gate')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(c.locator('.japanese-pdf-retry-unit')).to_have_count(3)
        expect(c.locator('.japanese-pdf-translation-proof').filter(has_text='AI申告 uncertain').first).to_contain_text('日本語0文字')
        expect(c.locator('.japanese-pdf-translation-proof').filter(has_text='日本語未生成').first).to_contain_text('AI申告 translated')
        assert all(u['classification']=='text' and u['text_flow']['glyph_verified'] for u in r['content_detection'][0]['text_units'] if u['role']=='abstract_body')
        expect(c.locator('.japanese-pdf-abstract-classification').first).to_contain_text('font単独では原物分類しない')
        c.screenshot(path=str(out/'lanes-mixed-diagnostics.png'))
        cases.append('glyph correspondence classification and uncertain versus English-response evidence are numerical and separate')
        mixed_pdf=req.get(base+f'/api/sources/{sid}/japanese-pdf/{mixed["generation_id"]}/file').body();(out/'mixed.pdf').write_bytes(mixed_pdf)
        with fitz.open(stream=mixed_pdf,filetype='pdf') as doc:
            text=norm(''.join(p.get_text() for p in doc))
            assert all(text.count(norm(PARTS[i]))==1 for i in (2,4,5))
        assert all(u['font_size']==11 for u in r['units'] if u['semantic_role']=='abstract_body')
        assert not any(a['kind']=='original_text' for a in r['assets'])
        cases.append('all failed ordinary paragraphs remain searchable 11pt English without any native text crop')
        req.post(base+'/test/mode/complete')
        failed=[u['id'] for u in r['semantic_units'] if u['ai_translation_state']=='failed']
        before_calls=req.get(base+'/test/calls').json()['count']
        for n,target in enumerate(failed,1):
            c.locator(f'.japanese-pdf-retry-unit[data-unit-id="{target}"]').click()
            expect(c.locator('.japanese-pdf-status')).to_contain_text('日本語再組版')
            expect(generate).to_be_enabled()
            expect(c.locator('.japanese-pdf-retry-unit')).to_have_count(3-n)
            calls=req.get(base+'/test/calls').json()
            assert calls['count']==before_calls+n and calls['ids'][-1]==target
        ready=job();r=ready['report'];assert r['reading_quality']['accepted'] and r['coverage']['abstract']['placed_translated_source_chars']==1150
        expect(c.locator('.japanese-pdf-status')).to_contain_text('読書品質ゲート通過')
        c.screenshot(path=str(out/'lanes-complete-diagnostics.png'))
        cases.append('three selected-unit actions each request only their failed ID and reuse every successful translation')
        c.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click();page.wait_for_selector('.pdf-page--ready .textLayer span')
        search=page.get_by_placeholder('文書内検索');search.fill('汎化');search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1');search.fill('')
        span=page.locator('.textLayer span').filter(has_text='汎化').first;span.scroll_into_view_if_needed()
        span.evaluate("s=>{const r=document.createRange();r.setStart(s.firstChild,0);r.setEnd(s.firstChild,2);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}")
        page.locator('.selection-popover button').click();expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        assert page.locator('.pdf-scroll').count()==1
        cases.append('Japanese search selection and original multi-block jump retain one viewer and SourceAnchor')
        with page.expect_download() as download:c.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        download.value.save_as(out/'japanese.pdf');pdf=(out/'japanese.pdf').read_bytes()
        with fitz.open(stream=pdf,filetype='pdf') as doc:
            text=norm(''.join(p.get_text() for p in doc));positions=[text.index(norm(TRANSLATIONS[p])) for p in PARTS]
            assert positions==sorted(positions) and all(text.count(norm(TRANSLATIONS[p]))==1 for p in PARTS)
        assert all(q['status']=='passed' and q['vector_preserved'] and q['glyphs_preserved'] and q['fonts_preserved'] and q['drawings_preserved'] and q['images_preserved'] and q['mean_pixel_difference']==0 for q in r['quality']['visuals'])
        cases.append('downloaded Japanese text has complete ordered coverage and native-3 figures tables equations remain exact')
        req.post(base+'/test/mode/damage');c.get_by_text('日本語版を試作',exact=True).evaluate('s=>{s.parentElement.open=true}');c.get_by_role('checkbox').check();generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('visual_content_changed')
        damaged=job();assert damaged['report']['quality']['failed_visuals']==1
        assert damaged['report']['diagnostics']['quality_validation_state']=='failed'
        assert not damaged['report']['reading_quality']['accepted']
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{damaged["generation_id"]}/file').status==404
        assert all(u['ai_translation_state']=='succeeded' for u in damaged['report']['semantic_units'] if u['role']=='paper_title')
        cases.append('one changed table line rejects quality and publication without relabelling successful AI titles')
        req.post(base+'/test/mode/restore');generate.click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('読書品質ゲート通過')
        saved=job();saved_pdf=req.get(base+f'/api/sources/{sid}/japanese-pdf/{saved["generation_id"]}/file').body()
        req.post(base+'/test/mode/offline');page.reload();expect(c.locator('.japanese-pdf-status')).to_contain_text('読書品質ゲート通過')
        assert req.get(base+f'/api/sources/{sid}/japanese-pdf/{saved["generation_id"]}/file').body()==saved_pdf
        after=req.get(base+f'/api/sources/{sid}/document').json()
        assert source_document['blocks']==after['blocks'] and source_document['version']==after['version']
        assert req.get(base+f'/api/sources/{sid}/file').body()==original
        cases.append('offline reload preserves saved artifacts original hash blocks and source version')
        assert not errors,errors;browser.close()
    original_images=render(original,out,'source');ja_images=render(pdf,out,'japanese');render(mixed_pdf,out,'mixed')
    for i,picture in enumerate(ja_images,1):
        asset=next((a for a in r['assets'] if a['japanese_page']==i),None)
        source_page=asset['source_page'] if asset else 1
        compare(original_images[source_page-1],picture,(f'Invented TeX source p.{source_page}',f'Japanese p.{i}'),out/f'lanes-comparison-{i}.png')
    if before:
        old=render(Path(before).read_bytes(),out,'before')
        with fitz.open(before) as old_doc:
            abstract_page=next(i for i,p in enumerate(old_doc) if norm(PARTS[0]) in norm(p.get_text()))
        compare(old[abstract_page],ja_images[0],('Old HEAD: protected Abstract','New: semantic text flow'),out/'lanes-before-after.png')
    report={'passed':len(cases),'cases':cases,'pageerrors':errors,'complete':r,
            'mixed':{k:mixed['report'][k] for k in ('coverage','diagnostics','reading_quality','semantic_units')},
            'damaged':{k:damaged['report'][k] for k in ('diagnostics','reading_quality','quality')}}
    (out/'lanes-browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'passed':len(cases),'pageerrors':errors,'abstract':r['coverage']['abstract']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path(tempfile.gettempdir())/'kg-lanes-browser');parser.add_argument('--before',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with server() as base:run(base,args.chromium,args.output,args.before)
