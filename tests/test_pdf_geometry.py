"""Phase 1 evidence tests using generated PDFs only."""
import json
from pathlib import Path
import subprocess

import fitz
import pytest

from app.ingest.common import block_ids
from app.ingest.pdf import extract_pdf
from tests.fixtures.pdf_factory import FIXTURE_NAMES, make_pdf


def resolve(anchor, blocks, version):
    # Exercise the actual build-free browser module, not a Python imitation.
    script = """
      import {resolveSourceAnchor} from './client/js/anchor.js';
      let input = ''; for await (const chunk of process.stdin) input += chunk;
      const [a,b,v] = JSON.parse(input);
      console.log(JSON.stringify(resolveSourceAnchor(a,b,v)));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            input=json.dumps([anchor, blocks, version]), text=True,
                            capture_output=True, check=True, timeout=10, cwd=Path(__file__).resolve().parents[1])
    return json.loads(result.stdout)


def document(name="simple", version="v1", **kwargs):
    extracted = extract_pdf(make_pdf(name, **kwargs))
    blocks = [dict(id=bid, version_id=version, idx=i, text=b.text, page=b.page,
                   bbox=b.bbox, kind=b.kind, role=b.role)
              for i, (bid, b) in enumerate(zip(block_ids(version, extracted.blocks), extracted.blocks))]
    return blocks, dict(id=version, content_hash="synthetic-hash")


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_page_bbox_and_span_coordinates(name):
    for b in extract_pdf(make_pdf(name)).blocks:
        g = b.bbox
        assert g["coordinate_system"] == "pymupdf_unrotated" and g["units"] == "pt"
        x0, y0, x1, y1 = g["rect"]
        assert 0 <= x0 < x1 <= 595 and 0 <= y0 < y1 <= 842
        assert b.page in (1, 2)
        for span in g["spans"]:
            a, z, c, d = span["rect"]
            assert x0 <= a < c <= x1 and y0 <= z < d <= y1


def test_column_order_and_no_cross_column_merges():
    blocks = extract_pdf(make_pdf("two_column")).blocks
    text = [b.text.split(".")[0] for b in blocks if "paragraph" in b.text]
    assert text == [f"{side} paragraph {i}" for side in ("LEFT", "RIGHT") for i in range(1, 9)]
    assert all(not ("LEFT" in b.text and "RIGHT" in b.text) for b in blocks)


def test_full_width_heading_separates_column_bands():
    text = [b.text.split(".")[0] for b in extract_pdf(make_pdf("column_bands")).blocks]
    divider = text.index("2 Full Width Section Between Columns")
    assert [t for t in text[:divider] if "paragraph" in t] == [
        f"{side} paragraph {i}" for side in ("LEFT", "RIGHT") for i in range(1, 5)]
    assert [t for t in text[divider:] if "paragraph" in t] == [
        f"{side} paragraph {i}" for side in ("LEFT", "RIGHT") for i in range(5, 9)]


def test_visual_candidates_and_captions_keep_independent_geometry():
    blocks = extract_pdf(make_pdf("visual_evidence")).blocks
    roles = {b.role: b for b in blocks if b.role}
    assert set(roles) == {"figure_candidate", "figure_caption", "table_candidate",
                          "table_caption", "equation_candidate"}
    assert roles["figure_candidate"].bbox["rect"] == [45, 180, 535, 260]
    assert roles["table_candidate"].bbox["rect"] == [40, 145, 555, 561]
    assert roles["figure_caption"].text.startswith("Figure 1.")
    assert roles["table_caption"].text.startswith("Table 1.")
    assert roles["equation_candidate"].text == "y = W x + b (1)"
    assert roles["table_candidate"].page == 2
    assert not any(b.role for b in blocks if b.text.startswith("Table 1 contains"))


def test_raster_image_region():
    blocks = extract_pdf(make_pdf("image_evidence")).blocks
    assert next(b for b in blocks if b.role == "figure_candidate").bbox["rect"] == [50, 180, 250, 380]


def test_rotated_cropped_geometry_remains_in_unrotated_page_space():
    data = make_pdf(rotation=90, crop=True)
    blocks = extract_pdf(data).blocks
    with fitz.open(stream=data, filetype="pdf") as doc:
        assert doc[0].rect.width == 802 and doc[0].rect.height == 555
    for b in blocks:
        if b.page == 1:
            assert b.bbox["rotation"] == 90
            assert b.bbox["page_rect"] == [0, 0, 555, 802]
            assert 0 <= b.bbox["rect"][0] < b.bbox["rect"][2] <= 555


def test_block_ids_stable_for_same_version_and_order_independent():
    a = extract_pdf(make_pdf()).blocks
    b = extract_pdf(make_pdf()).blocks
    assert block_ids("v1", a) == block_ids("v1", b)
    assert dict(zip((b.text for b in a), block_ids("v1", a))) == dict(
        zip((b.text for b in reversed(a)), block_ids("v1", list(reversed(a)))))
    assert not set(block_ids("v1", a)) & set(block_ids("v2", a))
    b[2].text += " changed"
    assert block_ids("v1", a)[2] != block_ids("v1", b)[2]
    assert len(set(block_ids("v1", [a[0], a[0]]))) == 2


def test_anchor_priority_quote_and_reparse():
    blocks, version = document()
    b = blocks[2]
    anchor = dict(sourceVersion="v1", blockId=b["id"], blockIdx=0, page=1,
                  bbox=b["bbox"], quote=b["text"], prefix="", suffix="")
    assert resolve(anchor, blocks, version)["method"] == "block_id"
    reparsed, new = document(version="v2")
    assert resolve(anchor, reparsed, new)["method"] == "bbox"
    moved, new = document(version="v2", shift=80)
    new["content_hash"] = "changed-hash"
    assert resolve(anchor, moved, new)["method"] == "quote"
    assert resolve(json.dumps(anchor), moved, new)["block"]["text"] == b["text"]
    legacy = dict(blockId=b["id"], blockIdx=0, page=1, quote=b["text"])
    assert resolve(legacy, blocks, version)["method"] == "quote"


def test_bbox_without_quote_requires_source_or_version_and_no_ambiguity():
    blocks, version = document()
    anchor = dict(page=1, bbox=blocks[2]["bbox"], sourceVersion="v1")
    assert resolve(anchor, blocks, version)["method"] == "bbox"
    duplicate = {**blocks[2], "id": "another"}
    result = resolve(anchor, blocks + [duplicate], version)
    assert result["status"] == "candidates" and result["block"] is None
    anchor["sourceVersion"] = "old"
    assert resolve(anchor, blocks, version)["status"] == "candidates"
    anchor["sourceHash"] = version["content_hash"]
    assert resolve(anchor, blocks, version)["method"] == "bbox"


def test_repeated_quote_context_and_wrong_geometry_never_guess():
    blocks, version = document("duplicate_evidence")
    anchor = dict(quote="Shared evidence phrase.", blockIdx=2, page=1)
    assert resolve(anchor, blocks, version)["status"] == "candidates"
    anchor.update(prefix="Beta before.", suffix="Beta after.")
    result = resolve(anchor, blocks, version)
    assert result["method"] == "quote" and result["block"]["text"].startswith("Beta")
    anchor.update(prefix="Missing context")
    assert resolve(anchor, blocks, version)["status"] == "candidates"
    # Stale ID and stale coordinates point to new text, so search the full quote.
    simple, v = document()
    target = simple[2]
    stale = dict(sourceVersion="old", blockId=simple[0]["id"], bbox=simple[0]["bbox"],
                 page=1, quote=target["text"], blockIdx=0)
    assert resolve(stale, simple, v)["block"]["id"] == target["id"]


def test_long_quote_not_truncated_and_repeated_occurrences_are_ambiguous():
    blocks = [dict(id="a", version_id="v", idx=0, text="x" * 80 + "ONE"),
              dict(id="b", version_id="v", idx=1, text="x" * 80 + "TWO")]
    assert resolve(dict(quote="x" * 80 + "TWO"), blocks, {"id": "v"})["block"]["id"] == "b"
    blocks[0]["text"] = "same same"
    assert resolve(dict(quote="same"), blocks, {"id": "v"})["status"] == "candidates"


def test_index_page_and_unresolved_are_not_verified_evidence():
    blocks, version = document()
    assert resolve(dict(blockIdx=2, sourceVersion="v1"), blocks, version)["method"] == "block_idx"
    assert resolve(dict(blockIdx=2), blocks, version)["status"] == "unresolved"
    result = resolve(dict(page=1, quote="missing", blockIdx=2, sourceVersion="old"), blocks, version)
    assert result["status"] == "page_only" and result["block"] is None
    assert resolve(dict(page=99), blocks, version)["status"] == "unresolved"
    assert resolve("broken JSON", blocks, version)["status"] == "unresolved"


def test_api_persists_geometry_and_version_scoped_ids(client):
    data = make_pdf("visual_evidence")
    sid = client.post("/api/sources/pdf", files={"file": ("generated.pdf", data)}).json()["source"]["id"]
    doc = client.get(f"/api/sources/{sid}/document").json()
    extracted = extract_pdf(data)
    assert [b["id"] for b in doc["blocks"]] == block_ids(doc["version"]["id"], extracted.blocks)
    assert all(json.loads(b["bbox_json"]) == b["bbox"] for b in doc["blocks"])
    assert any(b["role"] == "table_candidate" for b in doc["blocks"])
    assert client.get(f"/api/sources/{sid}/document").json()["blocks"] == doc["blocks"]


def test_changed_source_with_duplicate_quote_does_not_trust_unique_old_bbox():
    blocks, version = document("duplicate_evidence")
    block = next(b for b in blocks if b["text"].startswith("Alpha"))
    anchor = dict(sourceVersion="old", sourceHash="old-hash", page=1,
                  bbox=block["bbox"], quote="Shared evidence phrase.")
    assert resolve(anchor, blocks, version)["status"] == "candidates"
    anchor.update(prefix="Alpha before.", suffix="Alpha after.")
    assert resolve(anchor, blocks, version)["method"] == "bbox"


def test_database_new_extraction_preserves_user_evidence(client):
    from app.db import get_db
    from app.ingest.common import sha256_bytes
    from tests.test_smoke import _poll_status

    data = make_pdf()
    sid = client.post("/api/sources/pdf", files={"file": ("generated.pdf", data)}).json()["source"]["id"]
    assert _poll_status(client, sid) == "done"
    old = client.get(f"/api/sources/{sid}/document").json()
    block = old["blocks"][2]
    anchor = dict(sourceVersion=old["version"]["id"], sourceHash=old["version"]["content_hash"],
                  blockId=block["id"], blockIdx=block["idx"], page=1, bbox=block["bbox"], quote=block["text"])
    item = client.post("/api/knowledge", json=dict(source_id=sid, section_key="insights",
                       content="Preserved user evidence", origin="user", info_type="user_thought",
                       anchor=anchor)).json()["item"]
    question = client.post(f"/api/sources/{sid}/questions", json=dict(anchor=anchor,
                          selection_text=block["text"], question_text="Explain.")).json()["question"]
    translated = client.post("/api/translate", json=dict(source_id=sid, block_id=block["id"],
                              text=block["text"])).json()["translation"]
    # The current reanalyze API is AI extraction, not destructive PDF re-ingest.
    assert client.post(f"/api/sources/{sid}/reanalyze").status_code == 200
    assert _poll_status(client, sid) == "done"
    assert client.get(f"/api/sources/{sid}/document").json()["blocks"] == old["blocks"]
    # Model future parser re-extraction with a new version without implementing
    # a new re-ingest endpoint/UI or rewriting any saved research objects.
    moved = make_pdf(shift=80)
    doc = extract_pdf(moved)
    vid = "synthetic-reextraction"
    with get_db() as con:
        con.execute("INSERT INTO source_versions (id,source_id,fetched_at,content_hash) VALUES (?,?,?,?)",
                    (vid, sid, "2099-01-01T00:00:00+00:00", sha256_bytes(moved)))
        for i, (bid, b) in enumerate(zip(block_ids(vid, doc.blocks), doc.blocks)):
            con.execute("""INSERT INTO document_blocks
                (id,version_id,source_id,idx,kind,level,text,page,heading_path,bbox_json,role)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (bid, vid, sid, i, b.kind, b.level, b.text, b.page, b.heading_path, json.dumps(b.bbox), b.role))
    new = client.get(f"/api/sources/{sid}/document").json()
    assert new["version"]["id"] == vid
    assert resolve(anchor, new["blocks"], new["version"])["method"] == "quote"
    note = client.get(f"/api/sources/{sid}/note").json()
    restored = next(i for s in note["sections"] for i in s["items"] if i["id"] == item["id"])
    assert restored["anchor"] == anchor and restored["content"] == "Preserved user evidence"
    assert client.get(f"/api/sources/{sid}/questions").json()["questions"][0]["id"] == question["id"]
    assert new["translations"][block["id"]] == translated


def test_qa_context_does_not_follow_unverified_index(client):
    from contextlib import closing
    from app.db import get_db
    from app.context import anchor_context_index, surrounding_context

    sid = client.post("/api/sources/pdf", files={"file": ("repeated.pdf", make_pdf("duplicate_evidence"))}).json()["source"]["id"]
    doc = client.get(f"/api/sources/{sid}/document").json()
    b = next(b for b in doc["blocks"] if b["text"].startswith("Beta"))
    with closing(get_db()) as con:
        assert anchor_context_index(con, sid, dict(blockIdx=2)) is None
        assert anchor_context_index(con, sid, dict(blockIdx=2, quote="Shared evidence phrase.")) is None
        assert anchor_context_index(con, sid, dict(blockIdx=0, quote=b["text"])) == b["idx"]
        assert anchor_context_index(con, sid, dict(sourceVersion=doc["version"]["id"],
                                                  blockId=b["id"], blockIdx=0)) == b["idx"]
        # An older extraction with identical indices must not pollute context.
        con.execute("INSERT INTO source_versions (id,source_id,fetched_at) VALUES ('old-ctx',?,'2000-01-01')", (sid,))
        con.execute("INSERT INTO document_blocks (id,version_id,source_id,idx,kind,text) VALUES ('old-b','old-ctx',?,0,'para','Wrong old text')", (sid,))
        before, after, _ = surrounding_context(con, sid, b["idx"])
        assert "Wrong old text" not in before + after
    response = client.post(f"/api/sources/{sid}/questions", json={
        "anchor": {"blockIdx":2, "quote":"Shared evidence phrase."},
        "selection_text":"Shared evidence phrase.","question_text":"Explain."})
    assert response.status_code == 200
    summary = json.loads(response.json()["question"]["answers"][0]["context_summary"])
    assert summary["before_chars"] == summary["after_chars"] == 0
