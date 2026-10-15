"""One explicitly approved public-HTML check; never publishes paper content.

Raw HTML/assets/preview exist only under scratch. The separate report directory
contains allowlisted counts and booleans. No app server, database, AI, or PDF.
"""
from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PINNED = "2410.09453v3"
AUDIT_VERSION = "rdr-live-2"


# No artificial spaces at inline tag boundaries: <b>multi</b>modal is
# "multimodal", not "multi modal". Real whitespace, block boundaries and
# explicit line breaks remain significant; numbers/negation are not filtered.
TEXT_BOUNDARIES = frozenset("article section aside header footer div p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd figure figcaption table caption thead tbody tfoot tr th td pre blockquote".split())


def text(node):
    fragments = []
    def visit(current):
        if isinstance(current, Comment):
            return
        if isinstance(current, NavigableString):
            fragments.append(str(current))
        elif isinstance(current, Tag):
            if current.name in {"script", "style", "template"}:
                return
            # wbr has no separator. libxml may nest following text under it,
            # so visit its children as well rather than discarding that text.
            boundary = current.name in TEXT_BOUNDARIES or current.name == "br"
            if boundary:
                fragments.append("\n")
            for child in current.children:
                visit(child)
            if boundary:
                fragments.append("\n")
    visit(node)
    return " ".join("".join(fragments).split())


def text_delta(source, preview, index):
    """Bounded numeric diagnostics only; never emit either text or its hash."""
    result = {"index": index, "source_chars": len(source), "preview_chars": len(preview),
              "same_character_multiset": Counter(source) == Counter(preview)}
    if max(len(source), len(preview)) > 10000:
        result["edit_count_status"] = "size_limit"
        return result
    deleted = inserted = 0
    for operation, a, b, c, d in SequenceMatcher(None, source, preview, autojunk=True).get_opcodes():
        if operation in {"delete", "replace"}:
            deleted += b - a
        if operation in {"insert", "replace"}:
            inserted += d - c
    result.update(edit_count_status="measured", deleted_chars=deleted, inserted_chars=inserted)
    return result


def diagnostic_counts(acquired):
    # Arbitrary tag names/exception payloads in raw reports must not enter the
    # public Actions artifact. Only a fixed vocabulary and numeric counts do.
    vocabulary = frozenset("wbr cite q mark dfn samp var time script style iframe object embed form input button textarea select link meta base noscript template svg canvas video audio source unsafe_link".split())
    removed = Counter()
    for kind, count in acquired.get("removed_markup", {}).items():
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            removed[kind if kind in vocabulary else "other_markup"] += count
    return {"removed_markup": dict(removed),
            "duplicate_dom_id_count": len(acquired.get("duplicate_dom_ids", [])),
            "duplicate_block_count": len(acquired.get("duplicate_blocks", [])),
            "empty_region_count": len(acquired.get("empty_regions", [])),
            "warning_count": len(acquired.get("warnings", [])),
            "missing_reference_count": len(acquired.get("missing_references", []))}


def compare_dom(source, preview):
    """Compare against source DOM, not the extractor's own inventory.

    No source text, duplicate text, URLs, or error payloads escape this function.
    Even perfect agreement is not proof that arXiv HTML matches the local PDF.
    """
    issues, checks = [], {}
    for tag in ("h1", "h2", "h3", "p", "li", "figcaption", "th", "td"):
        original_nodes = source.find_all(tag)
        rendered_nodes = preview.find_all(tag)
        a = [text(n) for n in original_nodes]
        b = [text(n) for n in rendered_nodes]
        legacy_a = [" ".join(n.get_text(" ", strip=True).split()) for n in original_nodes]
        legacy_b = [" ".join(n.get_text(" ", strip=True).split()) for n in rendered_nodes]
        changed = [i for i, (left, right) in enumerate(zip(a, b)) if left != right]
        boundary_only = [i for i, (left, right) in enumerate(zip(a, b))
                         if left == right and legacy_a[i] != legacy_b[i]]
        checks[tag] = {"source_count": len(a), "preview_count": len(b),
                       "order_and_text_match": a == b,
                       "comparison": "html_text_stream_v1",
                       "legacy_order_and_text_match": legacy_a == legacy_b,
                       "changed_at_same_position": len(changed),
                       "unpaired_source_nodes": max(0, len(a) - len(b)),
                       "unpaired_preview_nodes": max(0, len(b) - len(a)),
                       "node_boundary_only_count": len(boundary_only),
                       "difference_details": [text_delta(a[i], b[i], i) for i in changed[:20]],
                       "difference_details_truncated": len(changed) > 20}
        if a != b:
            issues.append(tag + "_text_or_order_changed")
    a = [str(n.get("rowspan", "1")) + ":" + str(n.get("colspan", "1"))
         for n in source.find_all(["td", "th"])]
    b = [str(n.get("rowspan", "1")) + ":" + str(n.get("colspan", "1"))
         for n in preview.find_all(["td", "th"])]
    checks["table_cell_spans_match"] = a == b
    if a != b:
        issues.append("table_cell_spans_changed")
    for tag in ("img", "math", "figure", "table"):
        counts = [len(source.find_all(tag)), len(preview.find_all(tag))]
        checks[tag + "_counts"] = counts
        if counts[0] != counts[1]:
            issues.append(tag + "_count_changed")
    original_abstract = source.select_one(".ltx_abstract")
    safe_abstract = None
    if original_abstract is not None:
        original_text = text(original_abstract)
        for div in preview.find_all(["div", "section"]):
            if text(div) == original_text:
                safe_abstract = div
                break
        checks["abstract_source_chars"] = len(original_text)
    checks["abstract_text_found"] = safe_abstract is not None
    if safe_abstract is None:
        issues.append("abstract_text_not_verified")
    targets = {n["id"] for n in preview.find_all(id=True)}
    broken = sum(a["href"][1:] not in targets for a in preview.select('a[href^="#"]'))
    checks["broken_internal_links"] = broken
    if broken:
        issues.append("broken_internal_links")
    return checks, issues


def audit_export(directory, acquired, replay):
    result = {"audit_version": AUDIT_VERSION, "arxiv_id_version": PINNED,
              "acquisition_status": acquired.get("status", "missing"),
              "verdict": "blocked", "registered_pdf_match": "not_checked",
              "translation": "not_run", "database_access": "not_used",
              "checks": {}, "issues": []}
    if acquired.get("status") not in ("acquired", "conversion_incomplete"):
        result["issues"] = ["live_acquisition_blocked"]
        result["network_status_codes"] = [r["status"] for r in acquired.get("network_events", [])
                                          if isinstance(r.get("status"), int)]
        return result
    paths = [directory / name for name in ("source.html.txt", "reading_document.json", "english_preview.html")]
    if not all(p.is_file() for p in paths):
        result["issues"] = ["preview_files_missing"]
        return result
    raw = paths[0].read_bytes()
    document = json.loads(paths[1].read_text(encoding="utf-8"))
    source = BeautifulSoup(raw, "lxml").find("article")
    preview = BeautifulSoup(paths[2].read_text(encoding="utf-8"), "lxml").find("article")
    if source is None or preview is None:
        result["issues"] = ["article_missing"]
        return result
    checks, issues = compare_dom(source, preview)
    checks["pinned_version"] = document.get("source", {}).get("arxiv_id_version") == PINNED
    checks["source_hash_matches"] = (
        hashlib.sha256(raw).hexdigest() == document.get("source", {}).get("sha256") == acquired.get("sha256"))
    blocks = document.get("blocks", [])
    checks["block_order"] = [b.get("order") for b in blocks] == list(range(len(blocks)))
    checks["block_inventory"] = dict(Counter(b.get("kind", "unknown") for b in blocks))
    for name in ("pinned_version", "source_hash_matches", "block_order"):
        if not checks[name]:
            issues.append(name + "_failed")
    for required in ("title", "abstract", "heading", "paragraph", "figure", "table", "reference"):
        if not checks["block_inventory"].get(required):
            issues.append(required + "_block_missing")
    assets = acquired.get("assets", [])
    valid = 0
    for asset in assets:
        if asset.get("status") != "ok":
            continue
        p = (directory / asset.get("path", "")).resolve()
        if p.is_relative_to(directory.resolve()) and p.is_file():
            valid += hashlib.sha256(p.read_bytes()).hexdigest() == asset.get("sha256")
    checks["assets_total"] = len(assets)
    checks["assets_hash_verified"] = valid
    if not assets or valid != len(assets):
        issues.append("image_assets_incomplete")
    checks["acquisition_warning_count"] = len(acquired.get("warnings", []))
    checks["missing_reference_count"] = len(acquired.get("missing_references", []))
    checks["conversion_diagnostics"] = diagnostic_counts(acquired)
    if acquired.get("status") == "conversion_incomplete":
        issues.append("conversion_incomplete")
    checks["offline_replay"] = {
        "status": replay.get("status", "not_run"),
        "cache_reused": replay.get("cache_reused") is True,
        "network_requests": len(replay.get("network_events", [])),
    }
    if replay.get("status") not in ("acquired", "conversion_incomplete") or not replay.get("cache_reused") or replay.get("network_events"):
        issues.append("offline_replay_not_verified")
    result.update(checks=checks, issues=sorted(set(issues)),
                  verdict="needs_review" if issues else "structural_checks_passed")
    return result


# Source-free diagnostics identify whether the page itself overflows or a wide
# table/pre/math is safely contained in a local horizontal scroll area.
BROWSER_METRICS_JS = """() => {
    const safeTags = new Set(['header','main','article','section','div','span','p','a',
        'h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody','tr','th',
        'td','figure','figcaption','caption','img','math','pre','code','blockquote']);
    const all = [...document.querySelectorAll('header *, main *')];
    const round = value => Math.round(value * 100) / 100;
    const inLocalScroller = node => {
        for (let p = node.parentElement; p; p = p.parentElement) {
            const rect = p.getBoundingClientRect();
            if (['auto', 'scroll'].includes(getComputedStyle(p).overflowX) &&
                rect.left >= -1 && rect.right <= innerWidth + 1) return true;
        }
        return false;
    };
    const offenders = all.map((node, index) => ({node, index}))
        .filter(({node}) => {
            const r = node.getBoundingClientRect();
            return (r.right > innerWidth + 1 || r.left < -1) && !inLocalScroller(node);
        });
    return {
        width: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        images: document.querySelectorAll('main img').length,
        loadedImages: [...document.querySelectorAll('main img')].filter(i => i.complete && i.naturalWidth > 0).length,
        math: document.querySelectorAll('main math').length,
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
        articlePresent: !!document.querySelector('main article'),
        overflowElementCount: offenders.length,
        overflowElements: offenders.slice(0,20).map(({node, index}) => {
            const r = node.getBoundingClientRect(), name = node.tagName.toLowerCase();
            return {index, tag: safeTags.has(name) ? name : 'other',
                left: round(r.left), right: round(r.right), width: round(r.width),
                clientWidth: node.clientWidth, scrollWidth: node.scrollWidth};
        }),
        overflowDetailsTruncated: offenders.length > 20,
        localHorizontalScrollers: all.filter(n =>
            ['auto','scroll'].includes(getComputedStyle(n).overflowX) && n.scrollWidth > n.clientWidth + 1).length
    };
}"""


def browser_check(directory):
    """Render offline at two widths; return counts, no screenshots or text."""
    from playwright.sync_api import sync_playwright
    directory = directory.resolve()
    violations, errors, checks = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(offline=True, service_workers="block")
        def route_request(route):
            parts = urlsplit(route.request.url)
            local = (directory / unquote(parts.path).lstrip("/")).resolve()
            if parts.scheme != "https" or parts.netloc != "rdr01.invalid" or not local.is_relative_to(directory) or not local.is_file():
                violations.append(True)
                route.abort()
                return
            route.fulfill(path=str(local))
        context.route("**/*", route_request)
        page = context.new_page()
        page.on("pageerror", lambda _: errors.append(True))
        for width in (1280, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto("https://rdr01.invalid/english_preview.html", wait_until="load")
            page.evaluate("Promise.all(Array.from(document.images, i => i.decode().catch(() => null)))")
            checks.append(page.evaluate(BROWSER_METRICS_JS))
        browser.close()
    return {"viewports": checks, "page_error_count": len(errors),
            "blocked_request_count": len(violations),
            "passed": not errors and not violations and all(c["articlePresent"] and c["images"] == c["loadedImages"] and not c["horizontalOverflow"] for c in checks)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    scratch, reports = Path(args.scratch).resolve(), Path(args.report_dir).resolve()
    if scratch == reports or scratch.is_relative_to(reports) or reports.is_relative_to(scratch):
        parser.error("scratch and report-dir must be separate directories")
    reports.mkdir(parents=True, exist_ok=True)
    result = {"audit_version": AUDIT_VERSION, "arxiv_id_version": PINNED,
              "verdict": "blocked", "issues": [], "registered_pdf_match": "not_checked",
              "translation": "not_run", "database_access": "not_used"}
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            from app.reading_document.snapshot import snapshot
            directory, acquired = snapshot(PINNED, scratch)
            replay = {}
            if acquired.get("status") in ("acquired", "conversion_incomplete"):
                _, replay = snapshot(PINNED, scratch, offline=True)
            result = audit_export(directory, acquired, replay)
            if result["verdict"] != "blocked" and args.browser:
                result["browser"] = browser_check(directory)
                if not result["browser"]["passed"]:
                    result["issues"].append("browser_checks_failed")
                    result["verdict"] = "needs_review"
    except Exception as error:
        result.update(verdict="blocked", error_type=type(error).__name__)
        result["issues"].append("live_check_exception")
    (reports / "live_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = "\n".join([
        "## RDR-01 public MMAD v3 verification", "",
        "- Acquisition: " + result.get("acquisition_status", "not_completed"),
        "- Verdict: " + result["verdict"],
        "- Registered/local PDF matching: **not checked**",
        "- Translation/AI: **not run**; existing database: **not accessed**",
        "- Raw paper, images, and preview: runner scratch only; not published",
        "- A structural pass is not proof of source HTML/PDF equivalence", "",
        "```json", json.dumps(result, indent=2), "```", ""])
    (reports / "summary.md").write_text(summary, encoding="utf-8")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(summary)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["verdict"] == "structural_checks_passed" else 2 if result["verdict"] == "blocked" else 1


if __name__ == "__main__":
    raise SystemExit(main())
