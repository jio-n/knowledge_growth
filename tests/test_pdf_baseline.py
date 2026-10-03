"""Phase 0 PDF baseline; geometry/reading-order improvements belong to Phase 1."""
import json

import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ingest.pdf import extract_pdf
from tests.fixtures.pdf_factory import FIXTURE_NAMES, make_pdf
from tests.test_smoke import _poll_status


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_generated_pdf_is_reproducible_and_extractable(name):
    data = make_pdf(name)
    assert data == make_pdf(name)
    with fitz.open(stream=data, filetype="pdf") as pdf:
        assert len(pdf) == 2
        text = "\n".join(page.get_text() for page in pdf)
        if name == "two_column":
            assert "LEFT paragraph 1" in text and "RIGHT paragraph 8" in text
        if name == "visual_evidence":
            assert "Figure 1." in text and "Table 1." in text and "y = W x + b" in text
            assert len(pdf[0].get_drawings()) >= 7
            assert len(pdf[1].get_drawings()) >= 13
    doc = extract_pdf(data)
    assert doc.title == f"Synthetic Research Fixture: {name}"
    assert doc.authors == ["Fixture Author A", "Fixture Author B"]
    assert {b.page for b in doc.blocks} == {1, 2}
    assert any(b.kind == "heading" for b in doc.blocks)
    assert all(b.heading_path for b in doc.blocks)


def test_pdf_register_read_question_translate_note_export_and_reopen(client):
    data = make_pdf()
    response = client.post("/api/sources/pdf", files={"file": ("simple.pdf", data, "application/pdf")})
    assert response.status_code == 200, response.text
    sid = response.json()["source"]["id"]
    assert _poll_status(client, sid) == "done"
    file = client.get(f"/api/sources/{sid}/file")
    assert file.status_code == 200 and file.content == data
    assert file.headers["content-type"] == "application/pdf"
    document = client.get(f"/api/sources/{sid}/document").json()
    block = next(b for b in document["blocks"] if b["kind"] == "para")
    anchor = {"type": "text-quote", "blockId": block["id"], "blockIdx": block["idx"],
              "page": block["page"], "quote": block["text"][:60], "headingPath": block["heading_path"]}
    response = client.post(f"/api/sources/{sid}/questions", json={
        "anchor": anchor, "selection_text": block["text"], "question_text": "Explain this method."})
    assert response.status_code == 200, response.text
    question = response.json()["question"]
    assert question["anchor"] == anchor
    assert question["answers"][0]["provider"] == "mock"
    response = client.post("/api/translate", json={"source_id": sid, "block_id": block["id"], "text": block["text"]})
    assert response.status_code == 200, response.text
    translation = response.json()["translation"]
    assert translation and response.json()["cached"] is False
    cached = client.post("/api/translate", json={"source_id": sid, "block_id": block["id"], "text": block["text"]})
    assert cached.json() == {"translation": translation, "cached": True}
    response = client.post("/api/knowledge", json={
        "source_id": sid, "section_key": "insights", "content": "User synthetic memo",
        "origin": "user", "info_type": "user_thought", "anchor": anchor})
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    response = client.post("/api/highlights", json={"source_id": sid, "anchor": anchor, "comment": "Synthetic highlight"})
    assert response.status_code == 200, response.text
    hid = response.json()["id"]
    assert client.patch(f"/api/sources/{sid}", json={"reading_status": "reading"}).status_code == 200
    md = client.get(f"/api/sources/{sid}/export.md")
    assert md.status_code == 200 and "User synthetic memo" in md.text
    exported = client.get(f"/api/sources/{sid}/export.json").json()
    assert next(i for i in exported["knowledge_items"] if i["id"] == item["id"])["origin"] == "user"
    # New app lifespan/DB connections simulate reopening; browser localStorage is
    # checked separately because an API client cannot verify view/scroll restore.
    with TestClient(create_app()) as reopened:
        source = reopened.get(f"/api/sources/{sid}").json()["source"]
        assert source["reading_status"] == "reading" and source["last_opened_at"]
        restored_doc = reopened.get(f"/api/sources/{sid}/document").json()
        assert restored_doc["blocks"] == document["blocks"]
        assert restored_doc["translations"][block["id"]] == translation
        highlight = next(h for h in restored_doc["highlights"] if h["id"] == hid)
        assert json.loads(highlight["anchor"]) == anchor
        assert reopened.get(f"/api/sources/{sid}/questions").json()["questions"][0]["id"] == question["id"]
        note = reopened.get(f"/api/sources/{sid}/note").json()
        restored_item = next(i for s in note["sections"] for i in s["items"] if i["id"] == item["id"])
        assert restored_item["content"] == item["content"] and restored_item["anchor"] == anchor
        assert reopened.get(f"/api/sources/{sid}/file").content == data
    duplicate = client.post("/api/sources/pdf", files={"file": ("simple.pdf", data, "application/pdf")})
    assert duplicate.json()["duplicate"] is True
