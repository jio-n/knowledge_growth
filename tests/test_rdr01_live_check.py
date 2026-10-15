"""Safe-output live audit tests use only generated HTML and fake acquisition."""
import hashlib
import json
import pytest
from scripts.check_rdr01_live import PINNED, audit_export

SOURCE = '''<article><h1>Generated Example</h1><div class="ltx_abstract" id="a">
<h2>Abstract</h2><p>UNIQUE_PRIVATE_SENTINEL Example abstract.</p></div>
<h2>Introduction</h2><p>See <a href="#f">Figure</a>.</p>
<figure id="f"><img src="assets/example.png"><figcaption>Generated caption.</figcaption></figure>
<table><tr><th colspan="2">Group</th></tr><tr><td>Method</td><td>12.34</td></tr></table>
<math><mi>x</mi></math><h2>References</h2><ul><li>Generated reference.</li></ul></article>'''


def prepare(tmp_path):
    data = b"synthetic-image"
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/example.png").write_bytes(data)
    (tmp_path / "source.html.txt").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "english_preview.html").write_text(SOURCE.replace('class="ltx_abstract"', ''), encoding="utf-8")
    digest = hashlib.sha256(SOURCE.encode()).hexdigest()
    doc = {"source": {"arxiv_id_version": PINNED, "sha256": digest},
           "blocks": [{"kind": kind, "order": i} for i, kind in enumerate(
               ["title", "abstract", "heading", "paragraph", "figure", "table", "reference"])]}
    (tmp_path / "reading_document.json").write_text(json.dumps(doc), encoding="utf-8")
    acquired = {"status": "acquired", "sha256": digest, "assets": [
        {"path": "assets/example.png", "status": "ok", "sha256": hashlib.sha256(data).hexdigest()}]}
    replay = {"status": "acquired", "cache_reused": True, "network_events": []}
    return acquired, replay


def test_complete_structure_is_not_claimed_to_match_local_pdf(tmp_path):
    acquired, replay = prepare(tmp_path)
    result = audit_export(tmp_path, acquired, replay)
    assert result["verdict"] == "structural_checks_passed" and result["issues"] == []
    assert result["registered_pdf_match"] == "not_checked"
    assert result["translation"] == "not_run"
    assert result["checks"]["assets_hash_verified"] == 1
    assert "UNIQUE_PRIVATE_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("before,after,issue", [
    ("Example abstract.", "Changed abstract.", "p_text_or_order_changed"),
    ('colspan="2"', 'colspan="1"', "table_cell_spans_changed"),
    ('href="#f"', 'href="#lost"', "broken_internal_links"),
    ('<math><mi>x</mi></math>', '', "math_count_changed"),
    ('<img src="assets/example.png">', '', "img_count_changed"),
])
def test_source_differences_are_not_hidden_by_parser_inventory(tmp_path, before, after, issue):
    acquired, replay = prepare(tmp_path)
    path = tmp_path / "english_preview.html"
    path.write_text(path.read_text(encoding="utf-8").replace(before, after), encoding="utf-8")
    result = audit_export(tmp_path, acquired, replay)
    assert result["verdict"] == "needs_review" and issue in result["issues"]


def test_missing_asset_and_offline_request_detected(tmp_path):
    acquired, replay = prepare(tmp_path)
    (tmp_path / "assets/example.png").unlink()
    replay["network_events"] = [{"status": 200}]
    result = audit_export(tmp_path, acquired, replay)
    assert {"image_assets_incomplete", "offline_replay_not_verified"} <= set(result["issues"])


def test_identity_hash_and_order_are_gates(tmp_path):
    acquired, replay = prepare(tmp_path)
    path = tmp_path / "reading_document.json"
    data = json.loads(path.read_text())
    data["source"]["arxiv_id_version"] = "2410.09453v2"
    data["source"]["sha256"] = "bad"
    data["blocks"][0]["order"] = 100
    path.write_text(json.dumps(data))
    result = audit_export(tmp_path, acquired, replay)
    assert {"pinned_version_failed", "source_hash_matches_failed", "block_order_failed"} <= set(result["issues"])


@pytest.mark.parametrize("status", ["robots_denied", "robots_unavailable", "rate_limited", "temporary_failure"])
def test_blocked_source_has_safe_report_without_content(tmp_path, status):
    acquired = {"status": status, "detail": "PRIVATE_TOKEN_SENTINEL", "duplicate_blocks": ["PRIVATE_TEXT_SENTINEL"],
                "network_events": [{"url": "PRIVATE_URL_SENTINEL", "status": 429}]}
    result = audit_export(tmp_path, acquired, {})
    assert result["verdict"] == "blocked" and result["acquisition_status"] == status
    assert result["network_status_codes"] == [429]
    assert "SENTINEL" not in json.dumps(result)
