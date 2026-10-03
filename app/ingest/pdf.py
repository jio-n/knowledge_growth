"""PDF evidence geometry and conservative, column-aware text extraction.

Coordinates are unrotated PyMuPDF page coordinates in points, with a top-left
origin. No OCR, crop generation or semantic figure/table extraction is performed.
"""
from __future__ import annotations

import re
import statistics

import fitz

from .common import Block, ExtractedDoc, assign_heading_paths, parse_arxiv_id, parse_doi

_WS = re.compile(r"\s+")
_CAPTION = re.compile(r"^(Figure|Fig\.|Table)\s*\d+[a-z]?\s*[.:]", re.I)
_EQUATION = re.compile(r"^[\w\s+*/^().,−-]+\s*=\s*[^=]+(?:\(\d+\))?$")


def _norm(text):
    return _WS.sub(" ", text).strip()


def _rect(rect):
    return [round(float(v), 3) for v in rect]


def _union(rects):
    return [min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects)]


def _geometry(page, rect, spans=()):
    return {"coordinate_system": "pymupdf_unrotated", "units": "pt",
            "rect": _rect(rect), "page_rect": _rect(fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)),
            "rotation": page.rotation, "spans": list(spans)}


def _reading_order(blocks, width):
    """Find a central gutter, then read each horizontal band left -> right.

    Full-width blocks separate bands. An isolated heading above both columns is
    also a separator. Insufficient gutter evidence falls back to top -> bottom.
    """
    text = [b for b in blocks if b.role not in ("figure_candidate", "table_candidate")]
    body = [b for b in text if b.kind != "heading"]
    choices = []
    edges = sorted({b.bbox["rect"][0] for b in body} | {b.bbox["rect"][2] for b in body})
    for a, z in zip(edges, edges[1:]):
        cut = (a + z) / 2
        if z - a < width * .025 or not width * .25 < cut < width * .75:
            continue
        left = [b for b in body if b.bbox["rect"][2] <= a]
        right = [b for b in body if b.bbox["rect"][0] >= z]
        if len(left) >= 2 and len(right) >= 2:
            choices.append((len(left) + len(right), z - a, cut))
    if not choices:
        return sorted(blocks, key=lambda b: (b.bbox["rect"][1], b.bbox["rect"][0]))
    cut = max(choices)[2]
    separators, columns = [], []
    for b in blocks:
        x0, y0, x1, y1 = b.bbox["rect"]
        isolated_heading = b.kind == "heading" and not any(
            other is not b and other.bbox["rect"][1] < y1 and other.bbox["rect"][3] > y0
            for other in text)
        (separators if x0 < cut < x1 or isolated_heading else columns).append(b)
    result = []
    def flush(band):
        return sorted(band, key=lambda b: (b.bbox["rect"][0] >= cut,
                                           b.bbox["rect"][1], b.bbox["rect"][0]))
    for separator in sorted(separators, key=lambda b: (b.bbox["rect"][1], b.bbox["rect"][0])):
        above = [b for b in columns if b.bbox["rect"][3] <= separator.bbox["rect"][1]]
        result.extend(flush(above))
        columns = [b for b in columns if b not in above]
        result.append(separator)
    return result + flush(columns)


def extract_pdf(data: bytes) -> ExtractedDoc:
    with fitz.open(stream=data, filetype="pdf") as doc:
        meta = doc.metadata or {}
        # Preserve extraction block boundaries; never merge across columns/assets.
        pages = [page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
                 for page in doc]
        sizes = [s["size"] for d in pages for b in d["blocks"] if b["type"] == 0
                 for line in b["lines"] for s in line["spans"] if s["text"].strip()]
        body_size = statistics.median(sizes) if sizes else 10
        blocks = []
        for number, (page, extracted) in enumerate(zip(doc, pages), 1):
            page_blocks = []
            for raw in extracted["blocks"]:
                if raw["type"] != 0:
                    continue
                groups = []
                for line in raw["lines"]:
                    text = _norm("".join(s["text"] for s in line["spans"]))
                    if not text:
                        continue
                    size = max(s["size"] for s in line["spans"])
                    caption = _CAPTION.match(text)
                    role = ("table_caption" if caption and caption[1].lower() == "table" else
                            "figure_caption" if caption else
                            "equation_candidate" if len(text) < 160 and _EQUATION.fullmatch(text) else None)
                    heading = (not role and size >= body_size * 1.15 and len(text) < 120
                               and not text.endswith((".", ",", ";")))
                    kind = "heading" if heading else "para"
                    spans = [{"text": s["text"], "rect": _rect(s["bbox"])}
                             for s in line["spans"] if s["text"].strip()]
                    rect = _union([s["rect"] for s in spans])
                    # Some PDFs put both columns in one extraction block. Only
                    # combine aligned, adjacent lines with the same role/kind.
                    if (groups and groups[-1].kind == kind == "para" and groups[-1].role == role
                            and abs(groups[-1].bbox["rect"][0] - rect[0]) < 8
                            and 0 <= rect[1] - groups[-1].bbox["rect"][3] < body_size):
                        b = groups[-1]
                        b.text += " " + text
                        b.bbox["rect"] = _union([b.bbox["rect"], rect])
                        b.bbox["spans"].extend(spans)
                    else:
                        groups.append(Block(kind=kind, text=text, page=number, role=role,
                                            level=(1 if size >= body_size * 1.5 else 2) if heading else None,
                                            bbox=_geometry(page, rect, spans)))
                page_blocks.extend(groups)
            # Keep embedded-image and connected vector drawing regions as
            # candidates. They are geometric evidence, not verified semantics.
            regions = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
            for drawing in page.get_drawings():
                r = fitz.Rect(drawing["rect"])
                hits = [old for old in regions
                        if old.x0 - 3 <= r.x1 and old.x1 + 3 >= r.x0
                        and old.y0 - 3 <= r.y1 and old.y1 + 3 >= r.y0]
                if hits:
                    for old in hits:
                        r |= old
                        regions.remove(old)
                regions.append(r)
            for region in regions:
                region &= fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
                if region.width < 8 or region.height < 8:
                    continue
                captions = [b for b in page_blocks if b.role in ("figure_caption", "table_caption")
                            and b.bbox["rect"][0] < region.x1 and b.bbox["rect"][2] > region.x0
                            and min(abs(b.bbox["rect"][1] - region.y1),
                                    abs(b.bbox["rect"][3] - region.y0)) < 60]
                table = len(captions) == 1 and captions[0].role == "table_caption"
                page_blocks.append(Block(kind="table" if table else "figure", text="", page=number,
                                         role="table_candidate" if table else "figure_candidate",
                                         bbox=_geometry(page, region)))
            blocks.extend(_reading_order(page_blocks, page.cropbox.width))
        assign_heading_paths(blocks)
        head_text = "\n".join(b.text for b in blocks[:40])
        title = _norm(meta.get("title") or "")
        if not title:
            heading = next((b for b in blocks if b.kind == "heading"), None)
            title = heading.text if heading else next((b.text[:120] for b in blocks if b.text), "Untitled PDF")
        return ExtractedDoc(title=title, blocks=blocks,
                            authors=[a.strip() for a in _norm(meta.get("author") or "").split(";") if a.strip()],
                            doi=parse_doi(head_text), arxiv_id=parse_arxiv_id(head_text))
