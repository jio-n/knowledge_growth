import json
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from app.reading_document.acquire import AcquisitionError, Fetcher, identity, official_url
from app.reading_document.snapshot import snapshot
from tests.fixtures.reading_html_factory import PINNED, URL, html, image, transport


def run(tmp_path, markup=None, handler=None, offline=False):
    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(handler) if handler else transport(markup)), interval=0)
    return snapshot(PINNED, tmp_path, fetcher=fetcher, offline=offline)


def test_rich_structure_and_original_content(tmp_path):
    output, report = run(tmp_path)
    assert report["status"] == "acquired"
    doc = json.loads((output / "reading_document.json").read_text())
    blocks = doc["blocks"]
    assert [b["order"] for b in blocks] == list(range(len(blocks)))
    assert {b["kind"] for b in blocks} >= {"title", "metadata", "abstract", "heading", "paragraph", "list", "figure", "table", "equation", "footnote", "reference"}
    assert next(b for b in blocks if b["source_ref"]["dom_id"] == "S1-p1")["original_text"] == BeautifulSoup(html(), "lxml").find(id="S1-p1").get_text()
    original = BeautifulSoup(html(), "lxml").find("article")
    rendered = BeautifulSoup(doc["html"], "lxml").find("article")
    for tags in (["p"], ["td", "th"], ["li"], ["figcaption"]):
        assert [t.get_text() for t in rendered.find_all(tags)] == [t.get_text() for t in original.find_all(tags)]
    assert next(b for b in blocks if b["kind"] == "abstract")["original_text"].endswith("No translation or AI service is used.")
    table = next(b for b in blocks if b["source_ref"]["dom_id"] == "T1")
    assert table["tables"][0]["rows"][0][0]["rowspan"] == "2"
    assert table["tables"][0]["rows"][0][1]["colspan"] == "2"
    assert table["tables"][0]["rows"][3][1]["text"] == "91.25"
    assert table["captions"][0]["text"].startswith("Table 1.")
    equation = next(b for b in blocks if b["kind"] == "equation")
    assert equation["equations"][0]["tex"] == ["y = W x + b"]
    assert "<math" in equation["html"]
    assert "page" not in str([b["source_ref"] for b in blocks])
    assert report["asset_counts"] == {"total": 1, "ok": 1, "unresolved": 0}
    assert (output / report["assets"][0]["path"]).read_bytes() == image()
    assert doc["source"]["arxiv_id_version"] == PINNED  # bibliography ID ignored
    assert report["pdf_comparison"] == "not_checked"
    assert "100%" not in report["coverage"]


def test_offline_cache_has_no_network_and_preserves_bytes(tmp_path):
    output, _ = run(tmp_path)
    before = (output / "source.html.txt").read_bytes()
    def no_network(request):
        pytest.fail(f"Offline mode requested {request.url}")
    _, report = run(tmp_path, handler=no_network, offline=True)
    assert report["status"] == "acquired" and report["cache_reused"]
    assert report["network_events"] == []
    assert (output / "source.html.txt").read_bytes() == before
    (output / report["assets"][0]["path"]).unlink()
    _, report = run(tmp_path, handler=no_network, offline=True)
    assert report["status"] == "conversion_incomplete"
    assert report["assets"][0]["status"] == "offline_missing"
    assert "Missing image" in (output / "english_preview.html").read_text()


def test_hash_tampering_is_not_trusted(tmp_path):
    output, _ = run(tmp_path)
    (output / "source.html.txt").write_text("tampered")
    _, report = run(tmp_path, offline=True)
    assert report["status"] == "cache_invalid"
    assert not (output / "english_preview.html").exists()


@pytest.mark.parametrize("value", ["2410.09453", "https://arxiv.org/abs/2410.09453", "https://arxiv.org/html/2601.00001v2?x=1"])
def test_explicit_version_required(value):
    with pytest.raises(AcquisitionError): identity(value)


@pytest.mark.parametrize("value", ["http://arxiv.org/html/x", "https://evil.example/x", "https://arxiv.org.evil/x", "https://user@arxiv.org/x", "https://arxiv.org:444/x", "file:///tmp/x"])
def test_only_official_https_for_downloads(value):
    with pytest.raises(AcquisitionError): official_url(value)


@pytest.mark.parametrize(("change", "expected"), [
    (lambda s: s.replace('rel="canonical" href="' + URL, 'rel="canonical" href="https://arxiv.org/html/2601.00001v1'), "version_mismatch"),
    (lambda s: s.replace('rel="canonical" href="' + URL, 'rel="canonical" href="https://arxiv.org/html/2601.00001'), "version_unknown"),
    (lambda s: "<html><body>Abstract page only</body></html>", "conversion_missing"),
])
def test_document_identity_and_full_article(tmp_path, change, expected):
    _, report = run(tmp_path, markup=change(html()))
    assert report["status"] == expected
    assert report["resolved_url"] == URL and len(report["sha256"]) == 64
    assert report["acquired_at"]


@pytest.mark.parametrize(("status", "expected"), [(404, "html_unavailable"), (403, "forbidden"), (429, "rate_limited"), (503, "temporary_failure")])
def test_http_failure_states_no_retry(tmp_path, status, expected):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt": return httpx.Response(200, text="User-agent: *\nAllow: /")
        return httpx.Response(status)
    _, report = run(tmp_path, handler=handler)
    assert report["status"] == expected
    assert calls == ["https://arxiv.org/robots.txt", URL]


def test_temporary_connection_failure(tmp_path):
    def handler(request): raise httpx.ConnectError("network unavailable")
    _, report = run(tmp_path, handler=handler)
    assert report["status"] == "robots_unavailable"
    # Separately distinguish failure after successful robots acquisition.
    def handler2(request):
        if request.url.path == "/robots.txt": return httpx.Response(200, text="User-agent: *\nAllow: /")
        raise httpx.ConnectError("network unavailable")
    _, report = run(tmp_path, handler=handler2)
    assert report["status"] == "temporary_failure"


def test_robots_denial_prevents_article_request(tmp_path):
    def handler(request):
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /html/")
    _, report = run(tmp_path, handler=handler)
    assert report["status"] == "robots_denied"


@pytest.mark.parametrize(("target", "expected"), [("https://evil.example/x", "unsafe_url"),
    ("https://arxiv.org/html/2601.00001v1", "version_mismatch"),
    ("https://arxiv.org/html/2601.00001", "version_unknown"),
    ("https://arxiv.org/abs/2601.00001v2", "not_full_html")])
def test_redirect_validation(tmp_path, target, expected):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt": return httpx.Response(200, text="User-agent: *\nAllow: /")
        if str(request.url) == URL: return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text=html(), headers={"content-type": "text/html"})
    _, report = run(tmp_path, handler=handler)
    assert report["status"] == expected
    assert not any("evil.example" in c for c in calls)


def test_script_events_urls_and_svg_are_not_executable(tmp_path):
    hostile = '''<script>window.PWNED=1</script><iframe src="https://evil.example"></iframe>
    <p onclick="alert(1)" style="background:url(https://evil.example)"><a href="javascript:alert(1)">bad</a>
    <a href="data:text/html,bad">data</a><img src="https://evil.example/a.png" onerror="alert(1)"></p>
    <svg onload="alert(1)"><script>alert(1)</script></svg>'''
    output, report = run(tmp_path, markup=html(hostile))
    safe = BeautifulSoup((output / "english_preview.html").read_text(), "lxml")
    assert not safe.find(["script", "iframe", "svg"])
    assert not safe.select("[onclick], [onerror], [onload], [style]")
    assert not safe.select('a[href^="javascript:"], a[href^="data:"], img[src^="http"]')
    assert report["status"] == "conversion_incomplete"
    assert report["assets"][1]["status"] == "unsafe_url"


def test_invalid_base_ignored_and_missing_inventory_reported(tmp_path):
    source = html('<p id="dup">Repeat</p><p id="dup">Repeat</p><p></p><a href="#absent">Missing ref</a><span class="ltx_ERROR">Conversion failed</span>')
    source = source.replace(f'/html/{PINNED}/"', 'https://evil.example/"')
    output, report = run(tmp_path, markup=source)
    assert report["duplicate_dom_ids"] == ["dup"]
    assert report["duplicate_blocks"] == ["Repeat"]
    assert report["empty_regions"]
    assert report["missing_references"][0]["target"] == "absent"
    assert {w["kind"] for w in report["warnings"]} >= {"ignored_base", "html_conversion_warning"}
    assert (output / "english_preview.html").exists()


def test_missing_asset_and_asset_redirect(tmp_path):
    fixture = transport()
    def handler(request):
        if request.url.path.endswith("pipeline.png"):
            return httpx.Response(302, headers={"location": "http://arxiv.org/private.png"})
        return fixture.handle_request(request)
    _, report = run(tmp_path, handler=handler)
    assert report["assets"][0]["status"] == "unsafe_url"
    assert report["asset_counts"]["unresolved"] == 1


def test_no_database_or_ai_imports_and_pdf_identity_boundary(tmp_path):
    package = Path(__file__).parents[1] / "app" / "reading_document"
    for source in package.glob("*.py"):
        text = source.read_text()
        assert "import sqlite3" not in text and "from app.ai" not in text and "from app.db" not in text
    with pytest.raises(AcquisitionError, match="identity differs"):
        snapshot(PINNED, tmp_path, registered_pdf_identity="2601.00001v1")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(("status", "mime", "content", "expected"), [
    (404, "text/plain", b"Not found", "asset_missing"),
    (429, "text/plain", b"Slow down", "rate_limited"),
    (403, "text/plain", b"Forbidden", "forbidden"),
    (200, "image/svg+xml", b'<svg onload="alert(1)"></svg>', "unsupported_asset"),
    (200, "image/png", b"not really an image", "unsupported_asset"),
    (200, "image/png", b"\x89PNG\r\n\x1a\ncorrupt", "invalid_image"),
])
def test_individual_asset_failures(tmp_path, status, mime, content, expected):
    fixture = transport()
    def handler(request):
        if request.url.path.endswith("pipeline.png"):
            return httpx.Response(status, content=content, headers={"content-type": mime})
        return fixture.handle_request(request)
    output, report = run(tmp_path, handler=handler)
    assert report["assets"][0]["status"] == expected
    assert report["status"] == "conversion_incomplete"
    assert "Missing image" in (output / "english_preview.html").read_text()


def test_rate_limit_stops_remaining_asset_requests(tmp_path):
    calls = []
    fixture = transport(html('<img src="second.png" alt="Second image">'))
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("pipeline.png"): return httpx.Response(429)
        return fixture.handle_request(request)
    _, report = run(tmp_path, handler=handler)
    assert report["assets"][1]["status"] == "requests_stopped"
    assert not any(c.endswith("second.png") for c in calls)


def test_abstract_only_article_is_not_full_html(tmp_path):
    _, report = run(tmp_path, markup=f'<html><head></head><body><article><div class="ltx_abstract"><p>Abstract only</p></div></article></body></html>')
    assert report["status"] == "conversion_missing"


def test_cache_reused_online_without_fetch(tmp_path):
    run(tmp_path)
    def fail(request): pytest.fail("Valid local cache should not fetch")
    _, report = run(tmp_path, handler=fail)
    assert report["cache_reused"] and report["network_events"] == []


def test_equation_payload_is_sanitized(tmp_path):
    source = html().replace("<mi>y</mi>", '<mi onclick="alert(1)">y</mi><script>window.PWNED=1</script>')
    output, report = run(tmp_path, markup=source)
    doc = json.loads((output / "reading_document.json").read_text())
    math = next(b for b in doc["blocks"] if b["kind"] == "equation")["equations"][0]["mathml"]
    assert "script" not in math and "onclick" not in math
    assert report["removed_markup"]["script"] == 1


def test_latexml_equation_table_and_alttext(tmp_path):
    extra = '<table class="ltx_equation" id="eq-table"><tr><td><math alttext="x^2"><msup><mi>x</mi><mn>2</mn></msup></math></td></tr></table>'
    output, _ = run(tmp_path, markup=html(extra))
    document = json.loads((output / "reading_document.json").read_text())
    block = next(b for b in document["blocks"] if b["source_ref"]["dom_id"] == "eq-table")
    assert block["kind"] == "equation"
    assert block["equations"][0]["tex"] == ["x^2"]
    assert 'alttext="x^2"' in block["html"]
