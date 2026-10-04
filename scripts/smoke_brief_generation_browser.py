"""Generated-PDF / fake AIRuntime browser smoke; no account or API keys.

Optional dev dependencies: Playwright, system Chromium. No PDF/output/auth logs.
"""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright, expect
from tests.fixtures.brief_runtime import paper_pdf, field


@contextmanager
def server(no_ai=False, failing=False):
    with tempfile.TemporaryDirectory(prefix='kg-generate-browser-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = {k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory, KG_AI_RUNTIME='mock')
        program = '''
import time
import uvicorn
from app.main import create_app
from app import routes_sources
from app.ai.offline import NoAIRuntime
from tests.fixtures.brief_runtime import FakeBriefRuntime
class SlowFake(FakeBriefRuntime):
    def __init__(self):
        super().__init__(mutate=self.update_summary, invalid_attempts=999 if FAILING else 0)
        self.extractions = 0
    def update_summary(self, data, context):
        if context['stage'] == 'extract':
            self.extractions += 1
            if self.extractions > 1:
                data['paper_brief']['one_line_summary']['value'] = 'Updated synthetic classification summary'
    def send_turn(self, *args, **kwargs):
        time.sleep(.6)
        yield from super().send_turn(*args, **kwargs)
routes_sources.run_analysis_async = lambda *args: None
'''
        program += f'\nFAILING={failing!r}\n'
        program += f'uvicorn.run(create_app(runtime={"NoAIRuntime()" if no_ai else "SlowFake()"}), host="127.0.0.1", port={port}, log_level="error")'
        with open(Path(directory)/'server.log','w') as log:
            child = subprocess.Popen([sys.executable,'-c',program],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                deadline = time.monotonic()+20
                while True:
                    if child.poll() is not None: raise RuntimeError('smoke server startup failed')
                    try:
                        with socket.create_connection(('127.0.0.1',port),timeout=.2): break
                    except OSError:
                        if time.monotonic()>deadline: raise TimeoutError('smoke server startup timeout')
                        time.sleep(.1)
                yield f'http://127.0.0.1:{port}'
            finally:
                child.terminate(); child.wait(timeout=10)


def upload(request, base):
    res = request.post(base+'/api/sources/pdf',multipart={'file':{'name':'generated.pdf','mimeType':'application/pdf','buffer':paper_pdf()}})
    assert res.ok
    return res.json()['source']['id']


def run(base, chromium, output, cases, errors):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e: errors.append(str(e)))
        request = page.request
        sid = upload(request, base)
        page.goto(base+'/#/read/'+sid)
        page.get_by_role('button',name='Paper Brief',exact=True).click()
        panel = page.locator('[data-tab-panel="brief"]')
        expect(panel.locator('.brief-generation-state')).to_contain_text('未生成')
        panel.get_by_role('button',name='Paper Briefを生成',exact=True).click()
        expect(panel.locator('.brief-generation-state')).to_contain_text('生成中')
        expect(page.locator('.pdf-scroll')).to_be_visible()
        expect(panel.get_by_role('button',name='Paper Briefを生成',exact=True)).to_be_disabled()
        cases += ['initial entry', 'generating state and PDF available', 'duplicate start disabled']
        review = panel.locator('.brief-generation-review')
        expect(review.get_by_role('heading',name='初回生成 preview',exact=True)).to_be_visible()
        expect(review.get_by_role('button',name='確認したBriefを保存',exact=True)).to_be_disabled()
        assert request.get(base+f'/api/sources/{sid}/paper-brief').json()['paper_brief'] is None
        page.reload()
        expect(review.get_by_role('heading',name='初回生成 preview',exact=True)).to_be_visible()
        cases.append('preview reload can still confirm')
        expect(page.locator('.pdf-page canvas').first).to_be_visible()
        review.locator('.brief-generation-diff').evaluate('d=>d.scrollTop=0')
        page.screenshot(path=str(output/'generation-preview.png'))
        review.get_by_role('checkbox').check()
        review.get_by_role('button',name='確認したBriefを保存',exact=True).click()
        expect(panel.locator('.brief-generation-state')).to_contain_text('完了')
        expect(panel.get_by_role('heading',name='30秒Brief',exact=True)).to_be_visible()
        result = panel.locator('[data-result-id="r1"]').first
        for text in ('Synthetic-00','few-shot','Accuracy','62%','test','Baseline 60%'): expect(result).to_contain_text(text)
        result.get_by_role('button',name='📍 原文を見る',exact=True).click()
        page.wait_for_function("""() => {
          const m=document.querySelector('.pdf-evidence-marker'), p=document.querySelector('.pdf-scroll');
          if (!m || m.parentElement.dataset.pdfPage !== '3') return false;
          const a=m.getBoundingClientRect(), b=p.getBoundingClientRect(); return a.top>=b.top && a.bottom<=b.bottom;
        }""")
        page.screenshot(path=str(output/'generated-result-evidence.png'))
        cases += ['preview does not save', 'explicit confirmation', 'immediate 30-second Brief and Key Result', 'generated result Evidence jump']
        panel.get_by_text('Structured Brief · 詳細を開く',exact=True).click()
        figure = panel.locator('.brief-structured [data-field="important_figures"] .brief-result')
        expect(figure).to_contain_text('Figure 1')
        figure.get_by_role('button',name='📍 原文を見る',exact=True).click()
        page.wait_for_function("""() => {
          const m=document.querySelector('.pdf-evidence-marker'), p=document.querySelector('.pdf-scroll');
          if (!m || m.parentElement.dataset.pdfPage !== '4') return false;
          const a=m.getBoundingClientRect(), b=p.getBoundingClientRect(); return a.top>=b.top && a.bottom<=b.bottom;
        }""")
        table=panel.locator('.brief-structured [data-field="important_tables"] .brief-result')
        expect(table).to_contain_text('Table 1')
        table.get_by_role('button',name='📍 原文を見る',exact=True).click()
        page.wait_for_function("() => document.querySelector('.pdf-evidence-marker')?.parentElement.dataset.pdfPage === '4'")
        cases.append('structured Figure/Table caption Evidence jump')
        page.reload()
        expect(panel.locator('.brief-generation-state')).to_contain_text('完了')
        res = request.patch(base+f'/api/sources/{sid}/paper-brief/fields/research_objective',data=field('User objective stays','derived'))
        assert res.ok
        page.reload()
        panel.get_by_role('button',name='ユーザー編集を保持して再生成',exact=True).click()
        expect(review.get_by_role('heading',name='ユーザー編集を保持した再生成 preview',exact=True)).to_be_visible()
        preserved = review.locator('[data-field="research_objective"]')
        expect(preserved).to_have_attribute('data-action','preserve_user')
        preserved.get_by_text('現在の値',exact=True).click()
        expect(preserved).to_contain_text('User objective stays')
        page.screenshot(path=str(output/'regeneration-preserve.png'))
        cases += ['reload persists', 'regeneration mode and preserve user']
        # A fresh edit invalidates the preview, with no partial writes.
        res = request.patch(base+f'/api/sources/{sid}/paper-brief/fields/problem',data=field('New edit while preview open','derived'))
        assert res.ok
        review.get_by_role('checkbox').check()
        review.get_by_role('button',name='確認したBriefを保存',exact=True).click()
        expect(panel.locator('.brief-generation-state')).to_contain_text('previewが古くなりました')
        review.get_by_role('checkbox').check()
        expect(review.get_by_role('button',name='確認したBriefを保存',exact=True)).to_be_disabled()
        cases.append('stale preview blocked with edit preserved')
        panel.get_by_role('button',name='ユーザー編集を保持して再生成',exact=True).click()
        expect(review.get_by_role('heading',name='ユーザー編集を保持した再生成 preview',exact=True)).to_be_visible()
        review.get_by_role('checkbox').check()
        review.get_by_role('button',name='確認したBriefを保存',exact=True).click()
        expect(panel.locator('.brief-generation-state')).to_contain_text('完了')
        saved=request.get(base+f'/api/sources/{sid}/paper-brief').json()['paper_brief']['fields']
        assert saved['research_objective']['value']=='User objective stays'
        assert saved['problem']['value']=='New edit while preview open'
        cases.append('regenerate after stale and preserve both edits')
        panel.get_by_role('button',name='ユーザー編集を保持して再生成',exact=True).click()
        expect(review.get_by_role('heading',name='ユーザー編集を保持した再生成 preview',exact=True)).to_be_visible()
        # All content unchanged: no writable changes and confirmation stays disabled.
        review.get_by_role('checkbox').check()
        expect(review.get_by_role('button',name='確認したBriefを保存',exact=True)).to_be_disabled()
        cases.append('unchanged preview does not rewrite')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(output/'generation-mobile.png'))
        assert panel.evaluate('p=>p.scrollWidth<=p.clientWidth')
        cases.append('narrow viewport')
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output-dir',type=Path,default=Path('/tmp/kg-brief-generation-browser'))
    args = parser.parse_args(); args.output_dir.mkdir(parents=True,exist_ok=True)
    cases, errors = [], []
    with server() as base: run(base,args.chromium,args.output_dir,cases,errors)
    with server(no_ai=True) as base, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
        page=browser.new_page(); page.on('pageerror',lambda e: errors.append(str(e)))
        sid=upload(page.request,base)
        page.goto(base+'/#/read/'+sid)
        page.get_by_role('button',name='Paper Brief',exact=True).click()
        panel=page.locator('[data-tab-panel="brief"]')
        expect(panel.locator('.brief-generation-state')).to_contain_text('AI未接続')
        expect(panel.get_by_role('button',name='Paper Briefを生成',exact=True)).to_be_disabled()
        expect(panel.get_by_role('button',name='kgpackから取り込む',exact=True)).to_be_enabled()
        panel.get_by_role('button',name='ChatGPTで接続',exact=True).click()
        expect(page.locator('.ai-status__panel')).to_be_visible()
        expect(page.locator('.pdf-scroll')).to_be_visible()
        cases.append('no AI: PDF, import and ChatGPT connection entry')
        browser.close()
    with server(failing=True) as base, sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
        page=browser.new_page(); page.on('pageerror',lambda e: errors.append(str(e)))
        sid=upload(page.request,base)
        page.goto(base+'/#/read/'+sid)
        page.get_by_role('button',name='Paper Brief',exact=True).click()
        panel=page.locator('[data-tab-panel="brief"]')
        panel.get_by_role('button',name='Paper Briefを生成',exact=True).click()
        expect(panel.locator('.brief-generation-state')).to_contain_text('生成に失敗')
        expect(panel.get_by_role('button',name='Paper Briefを生成',exact=True)).to_be_enabled()
        expect(page.locator('.pdf-scroll')).to_be_visible()
        cases.append('bounded failure is retryable and PDF remains visible')
        browser.close()
    assert not errors, errors
    print(json.dumps({'passed':len(cases),'cases':cases,'pageerrors':errors,'screenshots':str(args.output_dir)},ensure_ascii=False))


if __name__ == '__main__': main()
