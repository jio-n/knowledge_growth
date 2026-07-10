"""PDF ingest via PyMuPDF.

Strategy: extract text per page, cluster lines into paragraph blocks,
detect headings heuristically (font size larger than body median).
Blocks keep their 1-based page number so anchors can jump back into the
rendered PDF (pdf.js) on the client.
"""
from __future__ import annotations

import re
import statistics

import fitz  # PyMuPDF

from .common import Block, ExtractedDoc, assign_heading_paths, parse_arxiv_id, parse_doi

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def extract_pdf(data: bytes) -> ExtractedDoc:
    doc = fitz.open(stream=data, filetype="pdf")
    meta = doc.metadata or {}
    blocks: list[Block] = []
    font_sizes: list[float] = []

    pages_spans: list[list[dict]] = []
    for page in doc:
        d = page.get_text("dict")
        spans = []
        for blk in d.get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                text = _norm("".join(s.get("text", "") for s in line.get("spans", [])))
                if not text:
                    continue
                size = max((s.get("size", 10) for s in line.get("spans", [])), default=10)
                spans.append({"text": text, "size": size, "block": id(blk)})
                font_sizes.append(size)
        pages_spans.append(spans)

    body_size = statistics.median(font_sizes) if font_sizes else 10.0
    heading_threshold = body_size * 1.15

    for pageno, spans in enumerate(pages_spans, start=1):
        buf: list[str] = []

        def flush():
            if buf:
                blocks.append(Block(kind="para", text=" ".join(buf), page=pageno))
                buf.clear()

        for s in spans:
            is_heading = (
                s["size"] >= heading_threshold
                and len(s["text"]) < 120
                and not s["text"].endswith((".", ",", ";"))
            )
            if is_heading:
                flush()
                level = 1 if s["size"] >= body_size * 1.5 else 2
                blocks.append(Block(kind="heading", level=level, text=s["text"], page=pageno))
            else:
                buf.append(s["text"])
                # end paragraph at sentence boundary followed by shortish line
                if s["text"].endswith(".") and len(s["text"]) < 60:
                    flush()
        flush()

    # merge consecutive tiny paras (line-per-block PDFs)
    merged: list[Block] = []
    for b in blocks:
        if (merged and b.kind == "para" and merged[-1].kind == "para"
                and merged[-1].page == b.page and len(merged[-1].text) < 300):
            merged[-1].text += " " + b.text
        else:
            merged.append(b)
    blocks = [b for b in merged if len(b.text) > 1]
    assign_heading_paths(blocks)

    head_text = "\n".join(b.text for b in blocks[:40])
    title = _norm(meta.get("title") or "")
    if not title:
        first_heading = next((b for b in blocks if b.kind == "heading"), None)
        title = first_heading.text if first_heading else (blocks[0].text[:120] if blocks else "Untitled PDF")

    authors = [a.strip() for a in _norm(meta.get("author") or "").split(";") if a.strip()]
    doc.close()
    return ExtractedDoc(
        title=title,
        blocks=blocks,
        authors=authors,
        doi=parse_doi(head_text),
        arxiv_id=parse_arxiv_id(head_text),
    )
