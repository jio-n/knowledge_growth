"""Synthetic regressions; no live paper, DB, AI or outbound browser requests."""
import base64
import json
import os
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from app.reading_document.acquire import Fetcher
from app.reading_document.snapshot import snapshot
from scripts.check_rdr01_live import BROWSER_METRICS_JS, compare_dom, diagnostic_counts, text_delta
from tests.fixtures.reading_html_factory import PINNED, html, transport


def parse(markup):
    return BeautifulSoup('<article>' + markup + '</article>', 'lxml').article


def export(tmp_path, extra):
    markup = html(extra)
    with httpx.Client(transport=transport(markup)) as client:
        directory, report = snapshot(PINNED, tmp_path, fetcher=Fetcher(client, interval=0))
    return markup, directory, report


@pytest.mark.parametrize('source,preview', [
    ('alpha<wbr>beta', 'alphabeta'),
    ('multi<cite>modal</cite>', 'multimodal'),
    ('91.<abbr>25</abbr>', '91.25'),
    ('https://example.invalid/<span>item</span>', 'https://example.invalid/item'),
])
def test_inline_node_boundaries_do_not_manufacture_word_changes(source, preview):
    checks, issues = compare_dom(parse('<p>' + source + '</p>'), parse('<p>' + preview + '</p>'))
    assert checks['p']['order_and_text_match']
    assert not checks['p']['legacy_order_and_text_match']
    assert checks['p']['node_boundary_only_count'] == 1
    assert 'p_text_or_order_changed' not in issues


@pytest.mark.parametrize('source,preview', [
    ('The result is not valid.', 'The result is valid.'),
    ('91.25', '91.52'), ('\u22121', '1'), ('a b', 'ab'),
    ('alpha<br>beta', 'alphabeta'), ('few-shot', 'zero-shot'),
    ('[1]', '[2]'), ('x&nbsp;y', 'xy'), ('\u00e9', 'e'),
])
def test_real_content_changes_still_fail_and_never_emit_source(source, preview):
    marker = 'PRIVATE_SOURCE_SENTINEL_'
    checks, issues = compare_dom(parse('<p>' + marker + source + '</p>'), parse('<p>' + marker + preview + '</p>'))
    assert not checks['p']['order_and_text_match']
    assert checks['p']['changed_at_same_position'] == 1
    assert 'p_text_or_order_changed' in issues
    assert checks['p']['difference_details'][0]['deleted_chars'] > 0
    assert marker not in json.dumps(checks)


def test_paragraph_reordering_and_missing_paragraph_are_rejected():
    source = parse('<p>First sentence.</p><p>Second sentence.</p>')
    reordered = parse('<p>Second sentence.</p><p>First sentence.</p>')
    checks, _ = compare_dom(source, reordered)
    assert checks['p']['changed_at_same_position'] == 2
    assert not checks['p']['order_and_text_match']
    checks, _ = compare_dom(source, parse('<p>First sentence.</p>'))
    assert checks['p']['unpaired_source_nodes'] == 1
    assert not checks['p']['order_and_text_match']


def test_numeric_difference_details_are_bounded():
    checks, _ = compare_dom(parse('<p>PRIVATE_BEFORE</p>' * 25), parse('<p>PRIVATE_AFTER</p>' * 25))
    assert checks['p']['changed_at_same_position'] == 25
    assert len(checks['p']['difference_details']) == 20
    assert checks['p']['difference_details_truncated']
    assert 'PRIVATE' not in json.dumps(checks)
    assert text_delta('x' * 10001, 'y', 0)['edit_count_status'] == 'size_limit'


def test_wbr_is_preserved_without_attributes_or_content_changes(tmp_path):
    extra = '<p id="token">https://example.invalid/<wbr onclick="alert(1)" style="display:none">exactToken</p>'
    markup, directory, report = export(tmp_path, extra)
    assert report['status'] == 'acquired' and 'wbr' not in report['removed_markup']
    doc = json.loads((directory / 'reading_document.json').read_text())
    safe = BeautifulSoup(doc['html'], 'lxml')
    assert safe.wbr is not None and not safe.wbr.attrs
    assert 'https://example.invalid/exactToken' in safe.get_text()
    assert '</wbr>' not in (directory / 'english_preview.html').read_text()
    assert next(b for b in doc['blocks'] if b['source_ref']['dom_id'] == 'token')['original_text'] == 'https://example.invalid/exactToken'
    assert (directory / 'source.html.txt').read_text() == markup


def test_unsupported_graphics_and_active_content_stay_flagged(tmp_path):
    _, directory, report = export(tmp_path, '<p>Before<svg><text>SCIENTIFIC_MARKER</text></svg>after<script>alert(1)</script></p>')
    assert report['status'] == 'conversion_incomplete'
    assert report['removed_markup']['svg'] == report['removed_markup']['script'] == 1
    safe = BeautifulSoup((directory / 'english_preview.html').read_text(), 'lxml')
    assert safe.svg is None and safe.script is None
    assert safe.select('.missing')


def test_conversion_diagnostics_explain_flags_without_arbitrary_strings():
    counts = diagnostic_counts({'removed_markup': {'wbr': 2, 'svg': 1, 'PRIVATE_ARBITRARY_NAME': 3},
                                'duplicate_blocks': ['PRIVATE_TEXT'], 'warnings': [{'text': 'PRIVATE_WARNING'}],
                                'empty_regions': ['PRIVATE_PATH'], 'missing_references': ['PRIVATE_URL']})
    assert counts['removed_markup'] == {'wbr': 2, 'svg': 1, 'other_markup': 3}
    assert counts['duplicate_block_count'] == counts['warning_count'] == counts['empty_region_count'] == 1
    assert 'PRIVATE' not in json.dumps(counts)


@pytest.mark.parametrize('width', [390, 1280])
def test_long_identifiers_wrap_but_tables_scroll_locally(tmp_path, width):
    playwright = pytest.importorskip('playwright.sync_api')
    url = 'https://example.invalid/' + 'W' * 40
    table = '<table><tr>' + ''.join('<td>VALUE_%02d_123456789</td>' % i for i in range(20)) + '</tr></table>'
    markup, directory, report = export(tmp_path, '<p><a href="'+url+'">'+url+'</a></p>'+table)
    view = (directory / 'english_preview.html').read_text()
    # set_content uses only in-memory synthetic HTML. Managed browser URL
    # navigation is unavailable locally. Embed *fixture* bytes for layout-only
    # testing; this does not change the production preview/CSP or fetch policy.
    view = view.replace("img-src 'self'", "img-src data:")
    for asset in report['assets']:
        raw = (directory / asset['path']).read_bytes()
        view = view.replace(asset['path'], 'data:'+asset['content_type']+';base64,'+base64.b64encode(raw).decode())
    with playwright.sync_playwright() as pw:
        executable = os.environ.get('RDR_TEST_CHROMIUM')
        if not executable and not Path(pw.chromium.executable_path).exists():
            pytest.skip('Chromium unavailable; set RDR_TEST_CHROMIUM or install Playwright Chromium')
        browser = pw.chromium.launch(**({'executable_path': executable} if executable else {}))
        try:
            context = browser.new_context(offline=True, service_workers='block', viewport={'width': width, 'height': 900})
            requests, errors = [], []
            context.route('**/*', lambda route: (requests.append(True), route.abort()))
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(True))
            page.set_content(view)
            page.evaluate('Promise.all(Array.from(document.images, i => i.decode()))')
            metrics = page.evaluate(BROWSER_METRICS_JS)
            assert not metrics['horizontalOverflow'] and metrics['overflowElementCount'] == 0
            assert metrics['localHorizontalScrollers'] >= 1
            assert metrics['images'] == metrics['loadedImages'] == 1
            assert not requests and not errors
            assert url in page.locator('article').text_content()
            assert page.locator('td').all_text_contents()[-20:] == ['VALUE_%02d_123456789' % i for i in range(20)]
            assert page.evaluate("getComputedStyle(document.body).overflowX") != 'hidden'
            assert page.evaluate("getComputedStyle(document.documentElement).overflowX") != 'hidden'
        finally:
            browser.close()
