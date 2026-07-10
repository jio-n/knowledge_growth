"""Markdown / plain-text ingest -> blocks."""
from __future__ import annotations

import re

from .common import Block, ExtractedDoc, assign_heading_paths

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^(```|~~~)")


def extract_text(content: str, filename: str | None = None) -> ExtractedDoc:
    lines = content.splitlines()
    blocks: list[Block] = []
    buf: list[str] = []
    code: list[str] | None = None

    def flush():
        if buf:
            text = " ".join(l.strip() for l in buf).strip()
            if text:
                blocks.append(Block(kind="para", text=text))
            buf.clear()

    for line in lines:
        if code is not None:
            if FENCE_RE.match(line):
                blocks.append(Block(kind="code", text="\n".join(code)))
                code = None
            else:
                code.append(line)
            continue
        if FENCE_RE.match(line):
            flush()
            code = []
            continue
        m = HEADING_RE.match(line)
        if m:
            flush()
            blocks.append(Block(kind="heading", level=len(m.group(1)), text=m.group(2).strip()))
        elif line.startswith(("- ", "* ", "+ ")) or re.match(r"^\d+\.\s", line):
            flush()
            blocks.append(Block(kind="list", text=line.lstrip("-*+ ").strip()))
        elif line.startswith(">"):
            flush()
            blocks.append(Block(kind="quote", text=line.lstrip("> ").strip()))
        elif not line.strip():
            flush()
        else:
            buf.append(line)
    flush()
    if code is not None and code:
        blocks.append(Block(kind="code", text="\n".join(code)))
    assign_heading_paths(blocks)

    first_heading = next((b for b in blocks if b.kind == "heading"), None)
    title = first_heading.text if first_heading else (filename or (blocks[0].text[:80] if blocks else "Untitled"))
    return ExtractedDoc(title=title, blocks=blocks)
