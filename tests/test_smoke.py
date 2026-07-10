"""End-to-end smoke test (mock provider, no network/keys) + a couple of
ingest unit tests. See CLAUDE.md — tests always run against the offline
mock LLM provider.
"""
from __future__ import annotations

import os
import tempfile

# Must be set before any `app.*` module is imported (app.config reads them
# at call time, but app.db.get_db()/DB_PATH resolve KG_DATA_DIR lazily —
# still, setting this up front keeps every module consistent).
os.environ["KG_DATA_DIR"] = tempfile.mkdtemp()
os.environ["KG_LLM_PROVIDER"] = "mock"

import time  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.ingest.common import Block, assign_heading_paths  # noqa: E402
from app.ingest.textfile import extract_text  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)

DOC_MARKDOWN = """# 研究テストペーパー

## Introduction

これはテスト用の導入段落です。十分な長さを持たせて段落ブロックとして認識されるようにしています。

## Method

提案手法についての説明段落です。ここではシンプルな例を用いて手法を説明します。

## Conclusion

結論として、この手法は有望な結果を示しており、今後の研究方向性を示唆しています。
"""


def _poll_status(source_id: str, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        r = client.get(f"/api/sources/{source_id}")
        assert r.status_code == 200, r.text
        status = r.json()["source"]["analysis_status"]
        if status in ("done", "error"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"analysis did not finish in time (last status={status})")


def test_full_workflow():
    # 1. register a text source
    r = client.post("/api/sources/text", json={"content": DOC_MARKDOWN})
    assert r.status_code == 200, r.text
    source = r.json()["source"]
    sid = source["id"]
    assert source["analysis_status"] == "pending"

    # 2. poll until analysis finishes (mock provider -> heuristic fallback, fast)
    status = _poll_status(sid)
    assert status == "done", status

    # 3. document has blocks with heading_path
    r = client.get(f"/api/sources/{sid}/document")
    assert r.status_code == 200, r.text
    doc = r.json()
    blocks = doc["blocks"]
    assert len(blocks) > 0
    assert any(b["kind"] == "heading" for b in blocks)
    para = next(b for b in blocks if b["kind"] == "para")
    assert para["heading_path"]  # non-empty: nested under a heading

    # 4. ask a question anchored to that paragraph block
    anchor = {
        "type": "text-quote",
        "blockId": para["id"],
        "blockIdx": para["idx"],
        "quote": para["text"][:40],
        "headingPath": para["heading_path"],
    }
    r = client.post(f"/api/sources/{sid}/questions", json={
        "anchor": anchor,
        "selection_text": para["text"],
        "prompt_type": "critique",
        "question_text": "",
    })
    assert r.status_code == 200, r.text
    question = r.json()["question"]
    assert question["anchor"]["blockId"] == para["id"]
    answers = question["answers"]
    assert len(answers) == 1
    answer = answers[0]
    assert answer["content"].strip()
    assert "モック" in answer["content"]
    assert answer["saved"] is False

    # 5. suggest a save target for (part of) the answer, then save it
    saved_content = answer["content"][:200]
    r = client.post("/api/knowledge/suggest", json={
        "source_id": sid, "content": saved_content, "prompt_type": "critique",
    })
    assert r.status_code == 200, r.text
    suggestion = r.json()
    assert suggestion["section_key"] == "limitations"
    assert suggestion["info_type"] == "llm_interpretation"

    r = client.post("/api/knowledge", json={
        "source_id": sid,
        "section_key": suggestion["section_key"],
        "title": suggestion["title"],
        "content": saved_content,
        "origin": "llm",
        "info_type": suggestion["info_type"],
        "anchor": anchor,
        "question_id": question["id"],
        "answer_id": answer["id"],
    })
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    item_id = item["id"]
    assert item["origin"] == "llm"
    assert item["anchor"]["blockId"] == para["id"]
    assert item["sort_order"] >= 1

    # 6. note shows the item under the right section
    r = client.get(f"/api/sources/{sid}/note")
    assert r.status_code == 200, r.text
    note = r.json()
    sec = next(s for s in note["sections"] if s["key"] == "limitations")
    assert any(i["id"] == item_id for i in sec["items"])
    # empty sections are still present
    assert any(s["items"] == [] for s in note["sections"])

    # 7. edit the saved content -> origin flips to llm_edited
    edited_content = saved_content + "\n\n(ユーザーによる追記)"
    r = client.patch(f"/api/knowledge/{item_id}", json={"content": edited_content})
    assert r.status_code == 200, r.text
    patched = r.json()["item"]
    assert patched["origin"] == "llm_edited"
    assert patched["content"] == edited_content

    # 8. export.md contains the saved content and the origin label
    r = client.get(f"/api/sources/{sid}/export.md")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    md = r.text
    assert "ユーザーによる追記" in md
    assert "出所" in md

    # download=1 sets a Content-Disposition header
    r = client.get(f"/api/sources/{sid}/export.md?download=1")
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")

    # 9. search finds the saved knowledge item
    needle = "追記"
    r = client.get("/api/search", params={"q": needle})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert any(res["kind"] == "knowledge" and res["ref_id"] == item_id for res in results)

    # 10. duplicate registration is detected
    r = client.post("/api/sources/text", json={"content": DOC_MARKDOWN, "force": False})
    assert r.status_code == 200, r.text
    dup = r.json()
    assert dup.get("duplicate") is True
    assert any(e["id"] == sid for e in dup["existing"])

    # 11. full JSON export works
    r = client.get(f"/api/sources/{sid}/export.json")
    assert r.status_code == 200, r.text
    sjson = r.json()
    assert sjson["source"]["id"] == sid
    assert any(k["id"] == item_id for k in sjson["knowledge_items"])

    r = client.get("/api/export/all.json")
    assert r.status_code == 200, r.text
    all_json = r.json()
    assert "exported_at" in all_json
    assert any(s["source"]["id"] == sid for s in all_json["sources"])


def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["provider"] == "mock"
    assert meta["model"] == "mock"
    assert isinstance(meta["note_template"], list) and meta["note_template"]
    keys = {p["key"] for p in meta["prompt_types"]}
    assert keys == {"explain", "explain_simple", "detail", "critique", "apply", "math", "free"}


def test_extract_text_headings_and_code():
    content = (
        "# タイトル\n\n"
        "導入の段落です。\n\n"
        "## サブセクション\n\n"
        "```python\n"
        "print('hello')\n"
        "```\n\n"
        "もう一つの段落です。\n"
    )
    doc = extract_text(content)
    kinds = [b.kind for b in doc.blocks]
    assert "heading" in kinds
    assert "code" in kinds
    assert "para" in kinds
    code_block = next(b for b in doc.blocks if b.kind == "code")
    assert "print('hello')" in code_block.text
    assert doc.title == "タイトル"
    sub = next(b for b in doc.blocks if b.kind == "heading" and b.level == 2)
    assert sub.heading_path == "タイトル > サブセクション"


def test_assign_heading_paths():
    blocks = [
        Block(kind="heading", level=1, text="A"),
        Block(kind="para", text="p1"),
        Block(kind="heading", level=2, text="B"),
        Block(kind="para", text="p2"),
        Block(kind="heading", level=1, text="C"),
        Block(kind="para", text="p3"),
    ]
    assign_heading_paths(blocks)
    assert blocks[1].heading_path == "A"
    assert blocks[3].heading_path == "A > B"
    assert blocks[5].heading_path == "C"
