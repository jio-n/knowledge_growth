"""T2A-04/T2-05 acceptance smoke using only generated PDF/kgpack, no API keys.

Starts an isolated mock server and removes its temporary DB on exit.
Requires optional Playwright and Chromium; neither is a frontend dependency.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright, expect
from app.import_bridge.schema import Package
from app.import_bridge.validator import generate_package
from tests.fixtures.pdf_factory import make_pdf
from tests.fixtures.kgpack_factory import package_data, field


@contextmanager
def mock_server():
    with tempfile.TemporaryDirectory(prefix='kg-brief-smoke-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY')}
        env.update(KG_DATA_DIR=directory, KG_LLM_PROVIDER='mock')
        with open(Path(directory) / 'server.log', 'w+') as log:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
                                      cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2):
                            break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise TimeoutError('Mock server startup timed out')
                        time.sleep(.1)
                yield f'http://127.0.0.1:{port}'
            finally:
                server.terminate()
                server.wait(timeout=10)


def run(base, chromium, output):
    cases = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.set_default_timeout(10000)
        page = context.new_page()
        errors = []
        context.on('page', lambda p: p.on('pageerror', lambda e: errors.append(str(e))))
        page.on('pageerror', lambda e: errors.append(str(e)))
        request = context.request
        assert request.get(base + '/api/meta').json()['provider'] == 'mock'
        cases.append('API-key-free mock')

        def upload(rotation=0):
            pdf = make_pdf('visual_evidence', rotation=rotation)
            response = request.post(base + '/api/sources/pdf', multipart={
                'file': {'name': 'generated.pdf', 'mimeType': 'application/pdf', 'buffer': pdf}})
            assert response.ok, response.text()
            return response.json()['source'], hashlib.sha256(pdf).hexdigest()

        source, source_hash = upload()
        sid = source['id']
        assert request.patch(base + f'/api/sources/{sid}', data={'title': 'Synthetic Research Fixture'}).ok
        assert request.patch(base + f'/api/sources/{sid}/paper-brief/fields/research_objective',
                             data=field('User objective: keep this.', status='derived')).ok
        data = package_data(source_hash)
        brief = data['paper_brief']
        brief.update(base_model=field(['Synthetic VLM'], status='derived'),
                     model_family=field(['VLM'], status='derived'),
                     learning_regimes=field(['few-shot', 'PEFT'], status='derived'),
                     adaptation_methods=field(['prompt tuning'], status='derived'),
                     datasets=field(['Synthetic-00'], status='derived'), metrics=field(['Accuracy'], status='derived'),
                     outputs=field(status='not_applicable'), model_size=field('Do not import', status='derived'),
                     novelty=field(['Review duplicate quote'], status='confirmed', evidence=['ev-candidates']),
                     limitations=field(['Missing evidence'], status='uncertain', evidence=['ev-missing']),
                     failure_cases=field(['Page hint only'], status='derived', evidence=['ev-page']))
        data['evidence_refs'] += [
            {'evidence_id': 'ev-candidates', 'page': 1, 'quote': 'Shared evidence phrase appears on both pages.'},
            {'evidence_id': 'ev-missing', 'page': 999, 'quote': 'No invented match.'},
            {'evidence_id': 'ev-page', 'page': 2, 'quote': 'No invented match.'}]
        data['knowledge_items'] = [{'id': 'note-1', 'section_key': 'background', 'title': 'Optional note', 'content': 'Archive only', 'evidence': []}]
        data['qa_threads'] = [{'id': 'qa-1', 'title': 'Optional QA', 'question': 'Invented question?', 'answer': 'Archive only', 'evidence': []}]
        data['manifest']['payloads'] += ['knowledge_items.json', 'qa_threads.json']
        package = generate_package(Package.model_validate(data))
        dialog = page.locator('.import-dialog')

        def begin(buffer, name='generated.kgpack'):
            page.goto(base + '/#/')
            with page.expect_file_chooser() as chooser:
                page.get_by_role('button', name='ChatGPTから取り込む', exact=True).click()
            chooser.value.set_files({'name': name, 'mimeType': 'application/octet-stream', 'buffer': buffer})
            dialog.get_by_role('button', name='Validateする', exact=True).click()

        def preview_next():
            dialog.get_by_role('button', name='Import Previewへ', exact=True).click()
            expect(dialog.locator('.import-fields')).to_be_visible()

        def confirm():
            dialog.get_by_role('button', name='確認へ', exact=True).click()
            expect(dialog.locator('.import-confirmation')).to_be_visible()

        def f(name):
            return dialog.locator(f'.import-field[data-field="{name}"]')

        begin(package)
        expect(dialog.get_by_text('Source matching: matched · strong · content_hash', exact=True)).to_be_visible()
        expect(dialog.get_by_text('この論文に取り込みます', exact=True)).to_be_visible()
        cases += ['valid validation', 'strong match']
        preview_next()
        expect(f('research_objective')).to_have_attribute('data-action', 'preserve_user')
        expect(f('research_objective')).to_contain_text('上書きできません')
        expect(f('research_objective')).to_contain_text('User objective: keep this.')
        page.route(base + '/api/import/kgpack/preview', lambda route: route.fulfill(status=500, json={'detail': 'Synthetic preview failure'}), times=1)
        f('model_size').get_by_role('checkbox').uncheck()
        expect(dialog.locator('.import-error')).to_contain_text('Synthetic preview failure')
        expect(f('model_size')).to_have_attribute('data-action', 'add')
        expect(dialog.get_by_role('button', name='確認へ', exact=True)).to_be_disabled()
        dialog.get_by_role('button', name='previewを再生成', exact=True).click()
        expect(f('model_size')).to_have_attribute('data-action', 'excluded')
        cases.append('failed exclusion preview retained and blocks confirmation')
        cases += ['field exclusion', 'user-edited preserve']
        expect(f('novelty').locator('[data-evidence-status="candidates"]')).to_be_visible()
        expect(f('limitations').locator('[data-evidence-status="unresolved"]')).to_be_visible()
        expect(f('failure_cases').locator('[data-evidence-status="page_only"]')).to_be_visible()
        expect(f('novelty')).to_contain_text('confirmed → uncertain')
        # Candidate browsing opens the actual PDF and does not persist a resolution.
        f('novelty').get_by_text('候補を見る（2件）', exact=True).click()
        with context.expect_page() as opened:
            f('novelty').get_by_role('link', name='候補 1 の原文を見る ↗', exact=True).click()
        candidate_page = opened.value
        expect(candidate_page.locator('.brief-navigation-notice')).to_contain_text('根拠未確定')
        expect(candidate_page.locator('.pdf-evidence-marker')).to_be_visible()
        candidate_page.screenshot(path=str(output / 'candidate.png'))
        candidate_page.close()
        existing = request.get(base + f'/api/sources/{sid}/paper-brief').json()['paper_brief']['fields']
        assert 'novelty' not in existing
        cases += ['candidates display and read-only browsing', 'unresolved display', 'page_only display', 'uncertain downgrade']
        expect(dialog.locator('.import-warnings')).to_contain_text('ノート・対話には未反映')
        dialog.screenshot(path=str(output / 'import-preview-fields.png'))
        dialog.evaluate('d=>d.scrollTop=0')
        dialog.screenshot(path=str(output / 'import-preview.png'))
        confirm()
        expect(dialog.locator('.import-confirmation')).to_contain_text('Preserved user field数')
        # A failed commit must retain the selected fields and allow explicit retry.
        commit_url = base + '/api/import/kgpack/commit'
        page.route(commit_url, lambda route: route.fulfill(status=500, json={'detail': 'Synthetic failure'}), times=1)
        dialog.get_by_role('button', name='取り込む', exact=True).click()
        expect(dialog.locator('.import-error')).to_contain_text('再試行できます')
        dialog.get_by_text('field差分・Evidenceをもう一度確認', exact=True).click()
        expect(f('model_size')).to_have_attribute('data-action', 'excluded')
        expect(dialog.get_by_role('button', name='取り込む', exact=True)).to_be_enabled()
        dialog.get_by_role('button', name='取り込む', exact=True).click()
        expect(dialog.get_by_text('取り込み完了', exact=True)).to_be_visible()
        dialog.get_by_role('button', name='Paper Briefを表示', exact=True).click()
        panel = page.locator('[data-tab-panel="brief"]')
        expect(panel.get_by_role('heading', name='30秒Brief', exact=True)).to_be_visible()
        saved = request.get(base + f'/api/sources/{sid}/paper-brief').json()['paper_brief']['fields']
        assert saved['research_objective']['value'] == 'User objective: keep this.'
        assert saved['research_objective']['origin'] == 'user'
        assert 'model_size' not in saved
        assert saved['novelty']['evidence_resolution'][0]['status'] == 'candidates'
        assert saved['key_results']['verification'] == 'unverified'
        cases += ['commit confirmation summary', 'commit failure/retry', 'commit success', 'candidate evidence not auto-confirmed']
        for status in ('confirmed', 'derived', 'uncertain', 'not_reported'):
            expect(panel.locator('.brief-status--' + status).first).to_be_visible()
        panel.get_by_text('Structured Brief · 詳細を開く', exact=True).click()
        expect(panel.locator('.brief-status--not_applicable')).to_be_visible()
        expect(panel.locator('[data-field="model_size"]').first).to_contain_text('未登録')
        result_card = panel.locator('[data-result-id="result-1"]').first
        for value in ('Synthetic-00', 'synthetic classification', 'few-shot', 'Accuracy', '62%', 'test', 'Synthetic baseline: 60.0%'):
            expect(result_card).to_contain_text(value)
        expect(panel.get_by_role('button', name='日本語版で読む · 今後対応', exact=True)).to_be_disabled()
        panel.evaluate('p=>p.scrollTop=0')
        page.screenshot(path=str(output / 'brief.png'))
        # Brief -> original PDF (including switching from the text mode).
        page.get_by_role('button', name='テキスト表示', exact=True).click()
        result_card.get_by_role('button', name='📍 原文を見る', exact=True).click()
        expect(page.locator('.pdf-scroll')).to_be_visible()
        expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        page.screenshot(path=str(output / 'evidence-jump.png'))
        page.reload()
        expect(panel.get_by_role('heading', name='30秒Brief', exact=True)).to_be_visible()
        expect(panel.locator('[data-field="research_objective"]').first).to_contain_text('User objective: keep this.')
        cases += ['Paper Brief display', 'all five statuses', 'Key Result context', 'Brief to PDF bbox jump', 'Brief persists after reload', 'Japanese CTA disabled']
        # A saved candidate remains a candidate; inspecting it again does not change revision.
        revision = request.get(base + f'/api/sources/{sid}/paper-brief').json()['paper_brief']['revision']
        novelty = panel.locator('[data-field="novelty"]').first
        novelty.get_by_text('由来・Evidenceを確認', exact=True).click()
        novelty.get_by_text('候補を見る（2件）', exact=True).click()
        novelty.get_by_role('button', name='候補 2 の原文を見る', exact=True).click()
        expect(page.locator('.brief-navigation-notice')).to_contain_text('根拠未確定')
        current = request.get(base + f'/api/sources/{sid}/paper-brief').json()['paper_brief']
        assert current['revision'] == revision
        assert current['fields']['novelty']['evidence_resolution'][0]['status'] == 'candidates'
        cases.append('persisted candidate browsing leaves evidence unchanged')
        failure_cases = panel.locator('.brief-structured [data-field="failure_cases"]')
        panel.get_by_text('Structured Brief · 詳細を開く', exact=True).click()
        failure_cases.get_by_role('button', name='p.2 を見る（根拠未確定）', exact=True).click()
        expect(page.locator('.brief-navigation-notice')).to_contain_text('ページのみ表示')
        expect(page.locator('.pdf-evidence-marker')).to_have_count(0)
        cases.append('page-only navigation clears resolved markers')
        # Duplicate package is surfaced and cannot reach confirmation.
        begin(package)
        preview_next()
        expect(dialog.locator('.import-warnings')).to_contain_text('package duplicate')
        expect(dialog.get_by_role('button', name='確認へ', exact=True)).to_be_disabled()
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        cases.append('duplicate package blocked')
        # Fresh update preview goes stale after a source metadata edit.
        update = deepcopy(data)
        update['manifest']['package_id'] = 'synthetic-update'
        update['paper_brief']['one_line_summary']['value'] = 'Updated synthetic summary <img src=x onerror=alert(1)>'
        update_package = generate_package(Package.model_validate(update))
        begin(update_package)
        preview_next()
        expect(f('one_line_summary')).to_have_attribute('data-action', 'update')
        expect(f('one_line_summary')).to_contain_text('<img src=x onerror=alert(1)>')
        assert f('one_line_summary').locator('img').count() == 0
        confirm()
        assert request.patch(base + f'/api/sources/{sid}', data={'title': 'Source changed after preview'}).ok
        dialog.get_by_role('button', name='取り込む', exact=True).click()
        expect(dialog.locator('.import-error')).to_contain_text('stale preview')
        expect(dialog.get_by_role('button', name='取り込む', exact=True)).to_be_disabled()
        dialog.get_by_text('field差分・Evidenceをもう一度確認', exact=True).click()
        expect(f('one_line_summary')).to_contain_text('An original synthetic method.')
        dialog.get_by_role('button', name='previewを再生成', exact=True).click()
        expect(dialog.get_by_role('button', name='確認へ', exact=True)).to_be_enabled()
        confirm()
        dialog.get_by_role('button', name='取り込む', exact=True).click()
        expect(dialog.get_by_text('取り込み完了', exact=True)).to_be_visible()
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        cases += ['existing field update warning', 'stale preview retained and blocked', 'regenerate and reconfirm', 'untrusted values render as text']
        # Two sources share a title; no automatic target is selected.
        second, _ = upload(rotation=90)
        sid2 = second['id']
        for source_id in (sid, sid2):
            assert request.patch(base + f'/api/sources/{source_id}', data={'title': 'Synthetic Research Fixture'}).ok
        ambiguous = package_data()
        ambiguous['manifest']['package_id'] = 'ambiguous-source'
        begin(generate_package(Package.model_validate(ambiguous)))
        expect(dialog.get_by_text('Source matching: ambiguous · weak · title', exact=True)).to_be_visible()
        expect(dialog.get_by_role('button', name='Import Previewへ', exact=True)).to_be_disabled()
        dialog.locator('#import-source-select').select_option(sid2)
        expect(dialog.get_by_text('この論文に取り込みます', exact=True)).to_be_visible()
        preview_next()
        confirm()
        dialog.get_by_role('button', name='取り込む', exact=True).click()
        expect(dialog.get_by_text('取り込み完了', exact=True)).to_be_visible()
        assert request.get(base + f'/api/sources/{sid2}/paper-brief').json()['paper_brief']
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        cases += ['ambiguous source blocked', 'manual source selection and commit']
        # A unique title is still weak and must not choose a target automatically.
        assert request.patch(base + f'/api/sources/{sid2}', data={'title': 'Different fixture title'}).ok
        weak = deepcopy(ambiguous)
        weak['manifest']['package_id'] = 'weak-source'
        weak['paper_brief']['one_line_summary']['value'] = 'Weak match generated summary'
        begin(generate_package(Package.model_validate(weak)))
        expect(dialog.get_by_text('Source matching: matched · weak · title', exact=True)).to_be_visible()
        expect(dialog.get_by_role('button', name='Import Previewへ', exact=True)).to_be_disabled()
        dialog.get_by_role('button', name='このPDFを選ぶ: Synthetic Research Fixture', exact=True).click()
        expect(dialog.get_by_role('button', name='Import Previewへ', exact=True)).to_be_enabled()
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        cases.append('weak match requires explicit candidate selection')
        # Unmatched requires an explicit existing PDF or registering a PDF first.
        unmatched = package_data()
        unmatched['manifest']['package_id'] = 'unmatched-source'
        unmatched['manifest']['source_identity']['title'] = 'Completely unrelated generated source'
        unmatched['paper_brief']['one_line_summary']['value'] = 'Unmatched synthetic summary'
        begin(generate_package(Package.model_validate(unmatched)))
        expect(dialog.get_by_text('Source matching: unmatched · — · 照合なし', exact=True)).to_be_visible()
        expect(dialog).to_contain_text('先にPDFを登録')
        expect(dialog.get_by_role('button', name='Import Previewへ', exact=True)).to_be_disabled()
        dialog.locator('#import-source-select').select_option(sid)
        expect(dialog.get_by_role('button', name='Import Previewへ', exact=True)).to_be_enabled()
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        cases.append('unmatched manual fallback')
        # Malformed kgpack retains the chooser and reports the validation failure.
        begin(b'not a package')
        expect(dialog.locator('.import-error')).to_contain_text('検証')
        expect(dialog.locator('input[type="file"]')).to_be_visible()
        dialog.get_by_role('button', name='閉じる', exact=True).click()
        cases.append('invalid package error')
        # Narrow viewport: dialog stays within the viewport and all controls remain usable.
        page.set_viewport_size({'width': 390, 'height': 844})
        begin(update_package)
        preview_next()
        bounds = dialog.bounding_box()
        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
        assert dialog.evaluate('d=>d.scrollWidth <= d.clientWidth')
        dialog.screenshot(path=str(output / 'mobile-preview.png'))
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        cases += ['narrow viewport', 'Escape closes dialog']
        assert not errors, errors
        print(json.dumps({'cases': cases, 'passed': len(cases), 'browser': 'Chromium', 'pageerrors': errors,
                          'screenshots': str(output)}, ensure_ascii=False))
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default=shutil.which('chromium'))
    parser.add_argument('--output-dir', type=Path, default=Path(tempfile.gettempdir()) / 'kg-import-brief-browser')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with mock_server() as base:
        run(base, args.chromium, args.output_dir)


if __name__ == '__main__':
    main()
