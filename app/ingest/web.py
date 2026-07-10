"""Web page ingest: fetch -> readability main content -> structured blocks.

The raw HTML is snapshotted to data/files so the version the user read is
preserved even if the page changes or disappears (§6). arXiv abs URLs get
light special-casing for metadata quality.
"""
from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup
from readability import Document as ReadabilityDoc

from .common import Block, ExtractedDoc, assign_heading_paths, parse_arxiv_id, parse_doi

UA = "Mozilla/5.0 (compatible; ResearchWorkspace/0.1; +local-first reading tool)"
BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "blockquote", "li", "table", "figcaption"]


def fetch_url(url: str) -> tuple[str, str]:
    """Return (final_url, html). Raises httpx.HTTPError on failure."""
    with httpx.Client(follow_redirects=True, timeout=30,
                      headers={"User-Agent": UA}) as client:
        r = client.get(url)
        r.raise_for_status()
        return str(r.url), r.text


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for n in names:
        tag = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def extract_web(url: str, html: str) -> ExtractedDoc:
    soup = BeautifulSoup(html, "lxml")

    title = _meta(soup, "citation_title", "og:title") or (soup.title.string.strip() if soup.title and soup.title.string else url)
    canonical = None
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        canonical = link["href"]
    site_name = _meta(soup, "og:site_name")
    published = _meta(soup, "article:published_time", "citation_publication_date", "date")
    authors = [m["content"].strip() for m in soup.find_all("meta", attrs={"name": "citation_author"}) if m.get("content")]
    if not authors:
        a = _meta(soup, "author", "article:author")
        if a:
            authors = [a]
    lang = (soup.html.get("lang") or "")[:2] if soup.html else None

    # main-content extraction
    try:
        main_html = ReadabilityDoc(html).summary(html_partial=True)
    except Exception:
        main_html = html
    main = BeautifulSoup(main_html, "lxml")

    blocks: list[Block] = []
    seen_texts: set[str] = set()
    for el in main.find_all(BLOCK_TAGS):
        if el.find(BLOCK_TAGS):  # keep leaf-most elements only (avoid li within table etc.)
            continue
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not text or len(text) < 2 or text in seen_texts:
            continue
        seen_texts.add(text)
        name = el.name
        if name.startswith("h") and len(name) == 2:
            blocks.append(Block(kind="heading", level=int(name[1]), text=text))
        elif name == "pre":
            blocks.append(Block(kind="code", text=el.get_text("\n", strip=True)))
        elif name == "blockquote":
            blocks.append(Block(kind="quote", text=text))
        elif name == "li":
            blocks.append(Block(kind="list", text=text))
        elif name == "table":
            blocks.append(Block(kind="table", text=text[:2000]))
        elif name == "figcaption":
            blocks.append(Block(kind="figure", text=text))
        else:
            blocks.append(Block(kind="para", text=text))
    assign_heading_paths(blocks)

    year = None
    if published:
        m = re.search(r"(19|20)\d{2}", published)
        if m:
            year = int(m.group(0))

    # arXiv special-casing: abstract page metadata
    arxiv_id = parse_arxiv_id(url) or parse_arxiv_id(canonical or "")
    doi = _meta(soup, "citation_doi") or parse_doi(html[:20000])

    return ExtractedDoc(
        title=title, blocks=blocks, authors=authors, year=year, lang=lang,
        site_name=site_name, published_at=published, canonical_url=canonical,
        doi=doi, arxiv_id=arxiv_id,
    )
