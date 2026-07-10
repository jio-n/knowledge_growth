"""Shared ingest utilities: block model, hashing, identifier parsing, dedup.

Every source type (pdf / web / text / markdown) is normalised into an
ordered list of Block dicts. Everything downstream (anchors, context
building, Q&A, note links) works on blocks only — this is the layer that
makes the app source-type agnostic (ADR-002).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class Block:
    kind: str                 # heading | para | code | quote | list | table | figure
    text: str
    level: int | None = None  # heading level
    page: int | None = None   # 1-based PDF page
    heading_path: str = ""


@dataclass
class ExtractedDoc:
    title: str
    blocks: list[Block]
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    lang: str | None = None
    site_name: str | None = None
    published_at: str | None = None
    canonical_url: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def doc_text(blocks: list[Block]) -> str:
    return "\n".join(b.text for b in blocks)


ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(v\d+)?", re.I)
ARXIV_ID_RE = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:a-z0-9]+)\b", re.I)


def parse_arxiv_id(text: str) -> str | None:
    m = ARXIV_RE.search(text)
    return m.group(1) if m else None


def parse_doi(text: str) -> str | None:
    m = DOI_RE.search(text)
    if not m:
        return None
    return m.group(1).rstrip(".,;)")


def assign_heading_paths(blocks: list[Block]) -> None:
    """Fill Block.heading_path from the running heading hierarchy."""
    stack: list[tuple[int, str]] = []
    for b in blocks:
        if b.kind == "heading":
            lvl = b.level or 1
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, b.text.strip()))
        b.heading_path = " > ".join(t for _, t in stack)


def find_duplicates(con, *, content_hash=None, doi=None, arxiv_id=None,
                    url=None, canonical_url=None) -> list[dict]:
    """Return existing sources that look like the same material (§5)."""
    clauses, params = [], []
    if content_hash:
        clauses.append("content_hash = ?"); params.append(content_hash)
    if doi:
        clauses.append("doi = ?"); params.append(doi)
    if arxiv_id:
        clauses.append("arxiv_id = ?"); params.append(arxiv_id)
    for u in {url, canonical_url} - {None, ""}:
        clauses.append("(url = ? OR canonical_url = ?)"); params += [u, u]
    if not clauses:
        return []
    sql = "SELECT * FROM sources WHERE " + " OR ".join(clauses)
    return [dict(r) for r in con.execute(sql, params).fetchall()]
