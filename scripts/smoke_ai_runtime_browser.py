"""Phase 0A browser acceptance: real fake subprocess, no account/API key/network.

Requires optional Playwright and Chromium, as existing browser smoke does.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright, expect
from smoke_import_brief_browser import mock_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output-dir',type=Path,default=Path('/tmp/kg-ai-runtime-browser'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    cases = []
    with tempfile.TemporaryDirectory(prefix='kg-fake-codex-') as directory:
        executable = Path(directory)/'codex'
        executable.write_text(f'#!{sys.executable}\nCONFIG = {{"mode":"browser"}}\n' +
                              (ROOT/'tests/fixtures/app_server/fake_server.py').read_text())
        executable.chmod(0o700)
        with patch.dict('os.environ',{'KG_AI_RUNTIME':'codex_chatgpt_plan','KG_CODEX_EXECUTABLE':str(executable)}), mock_server() as base:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
                page = browser.new_page(viewport={'width':1440,'height':1000})
                errors = []
                page.on('pageerror',lambda error: errors.append(str(error)))
                page.goto(base)
                expect(page.locator('.ai-status summary')).to_contain_text('ChatGPT未接続')
                page.locator('.ai-status summary').click()
                panel = page.locator('.ai-status__panel')
                expect(panel).to_contain_text('PDF・Import済みBrief・Note・Highlight・export')
                expect(panel.locator('input')).to_have_count(0)
                page.get_by_role('button',name='ChatGPTで接続',exact=True).click()
                link = page.get_by_role('link',name='ブラウザで認証を開く ↗',exact=True)
                expect(link).to_have_attribute('href','https://auth.openai.com/oauth/authorize?state=fixture')
                expect(link).to_have_attribute('rel','noopener noreferrer')
                page.screenshot(path=str(args.output_dir/'auth-pending.png'))
                cases += ['subscription-first status', 'no API-key inputs', 'login pending and safe browser link']
                page.get_by_role('button',name='ログインをキャンセル',exact=True).click()
                expect(panel).to_contain_text('ログインをキャンセルしました')
                expect(link).to_have_count(0)
                cases.append('login cancellation')
                page.get_by_role('button',name='ChatGPTで接続',exact=True).click()
                expect(page.locator('.ai-status summary')).to_contain_text('接続済み')
                expect(link).to_have_count(0)
                page.get_by_role('button',name='最小接続テスト',exact=True).click()
                expect(page.locator('.ai-smoke-output')).to_have_text('runtime-ok')
                expect(page.get_by_role('button',name='最小接続テスト',exact=True)).to_be_enabled()
                cases += ['login completion notification', 'one turn streaming in UI']
                page.get_by_role('button',name='最小接続テスト',exact=True).click()
                cancel = page.get_by_role('button',name='接続テストを中断',exact=True)
                expect(cancel).to_be_enabled()
                cancel.click()
                expect(page.locator('.ai-smoke-output')).to_contain_text('中断しました')
                cases.append('stream interruption')
                page.get_by_role('button',name='再接続',exact=True).click()
                expect(page.locator('.ai-status summary')).to_contain_text('接続済み')
                cases.append('restart authentication reuse')
                page.get_by_role('button',name='ログアウト',exact=True).click()
                expect(page.locator('.ai-status summary')).to_contain_text('ChatGPT未接続')
                expect(page.get_by_role('button',name='最小接続テスト',exact=True)).to_be_disabled()
                page.set_viewport_size({'width':390,'height':844})
                bounds = panel.bounding_box()
                assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
                page.screenshot(path=str(args.output_dir/'signed-out-mobile.png'))
                cases += ['logout and AI-only controls disabled', 'narrow viewport']
                assert not errors, errors
                browser.close()
        with patch.dict('os.environ',{'KG_AI_RUNTIME':'codex_chatgpt_plan','KG_CODEX_EXECUTABLE':'/missing/kg-codex'}), mock_server() as base:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox'])
                page = browser.new_page()
                page.goto(base)
                expect(page.locator('.ai-status summary')).to_contain_text('Codex未インストール')
                page.locator('.ai-status summary').click()
                expect(page.get_by_role('link',name='セットアップ方法 ↗',exact=True)).to_be_visible()
                expect(page.get_by_role('button',name='ChatGPTから取り込む',exact=True)).to_be_enabled()
                cases.append('Codex missing keeps Library/Import available')
                browser.close()
    print(json.dumps({'passed':len(cases),'cases':cases,'pageerrors':errors,'screenshots':str(args.output_dir)},ensure_ascii=False))


if __name__ == '__main__': main()
