"""Conservative semantic inventory and allowlisted rich HTML, never raw insertion."""
from __future__ import annotations

import re
from collections import Counter
from html import escape
from urllib.parse import urljoin, urlsplit, unquote

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from . import PARSER_VERSION
from .acquire import AcquisitionError, digest, identity, official_url

HTML_TAGS = set("article section aside header footer div span p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd figure figcaption table caption thead tbody tfoot tr th td a em strong b i u s sub sup br hr pre code blockquote small abbr img wbr cite".split())
MATH_TAGS = set("math semantics annotation mrow mi mn mo ms mtext mspace msup msub msubsup mfrac msqrt mroot munder mover munderover mtable mtr mtd menclose mpadded mphantom mfenced mmultiscripts mprescripts none".split())
DROP = set("script style iframe object embed form input button textarea select link meta base noscript template svg canvas video audio source".split())


def role(tag):
    classes = set(tag.get("class", []))
    if "ltx_abstract" in classes: return "abstract"
    if tag.name == "h1": return "title"
    if tag.name in ("h2", "h3", "h4", "h5", "h6"): return "heading"
    if tag.name == "figure": return "table" if "ltx_table" in classes else "figure"
    if tag.name == "math" or classes & {"ltx_equation", "ltx_equationgroup"}: return "equation"
    if tag.name == "table": return "table"
    if classes & {"ltx_note", "ltx_note_content"}: return "footnote"
    if "ltx_bibitem" in classes: return "reference"
    if classes & {"ltx_authors", "ltx_dates", "ltx_classification", "ltx_keywords"}: return "metadata"
    if tag.name in ("ul", "ol", "dl") and "ltx_biblist" not in classes: return "list"
    if tag.name == "p": return "paragraph"
    return None


def dom_path(tag):
    segments = []
    while isinstance(tag, Tag) and tag.name != "[document]":
        siblings = [x for x in tag.previous_siblings if isinstance(x, Tag) and x.name == tag.name]
        segments.append(f"{tag.name}:nth-of-type({len(siblings) + 1})")
        tag = tag.parent
    return " > ".join(reversed(segments))


def document_identity(soup, requested, resolved):
    # Only document-level declarations; never search body text or bibliography IDs.
    resolved_id = identity(resolved)
    if urlsplit(resolved).path != f"/html/{resolved_id}":
        raise AcquisitionError("not_full_html", "Response resolved to a non-HTML document")
    if resolved_id != requested:
        raise AcquisitionError("version_mismatch", f"Requested {requested}, resolved {resolved_id}")
    evidence = [{"kind": "resolved_url", "value": resolved}]
    for node in soup.select('head link[rel~="canonical"], head meta[name="citation_arxiv_id"]'):
        declared = node.get("href") if node.name == "link" else node.get("content")
        try:
            declared_id = identity(declared or "")
        except AcquisitionError as error:
            raise AcquisitionError("version_unknown", "Document declaration has no pinned version") from error
        if declared_id != requested:
            raise AcquisitionError("version_mismatch", f"Document declares {declared_id}")
        evidence.append({"kind": node.name, "value": declared})
    return evidence


def parse_html(raw, requested, resolved, load_asset):
    soup = BeautifulSoup(raw, "lxml")
    evidence = document_identity(soup, requested, resolved)
    article = soup.select_one("article.ltx_document") or soup.find("article")
    if not article or not article.find("p"):
        raise AcquisitionError("conversion_missing", "No full article/paragraph content (abstract page is insufficient)")
    if not any(not p.find_parent(class_="ltx_abstract") for p in article.find_all("p")):
        raise AcquisitionError("conversion_missing", "Article contains only an abstract")
    warnings = []
    base = resolved
    if soup.base and soup.base.get("href"):
        candidate = urljoin(resolved, soup.base["href"])
        try:
            official_url(candidate)
            # A base may locate assets, but must not silently change the paper/version.
            path = urlsplit(candidate).path
            prefix = f"/html/{requested}"
            if not (path == prefix or path.startswith(prefix + "/")):
                raise AcquisitionError("unsafe_url", "base leaves pinned paper path")
            base = candidate
        except (AcquisitionError, ValueError):
            warnings.append({"kind": "ignored_base", "url": candidate})

    all_tags = list(article.find_all(True))
    ids = [t["id"] for t in [article, *all_tags] if t.get("id")]
    counts = Counter(ids)
    # Rewrite EVERY source ID so names cannot collide with shell UI anchors.
    anchors = {name: f"source-{i}" for i, name in enumerate(dict.fromkeys(ids))}
    seen_ids = set()
    assets = {}
    missing_refs = []
    removed = Counter()

    def render(node):
        if isinstance(node, Comment): return ""
        if isinstance(node, NavigableString): return escape(str(node))
        if not isinstance(node, Tag): return ""
        name = node.name
        if name in DROP:
            removed[name] += 1
            return '<span class="missing">[Unsupported original graphic; inspect the original PDF]</span>' if name in {"svg", "canvas", "object", "embed"} else ""
        children = lambda: "".join(render(c) for c in node.children)
        if name not in HTML_TAGS | MATH_TAGS:
            removed[name] += 1
            return children()
        attrs = {}
        if node.get("id") and node["id"] not in seen_ids:
            attrs["id"] = anchors[node["id"]]
            seen_ids.add(node["id"])
        for attr in ("title", "lang", "dir"):
            if node.get(attr): attrs[attr] = node[attr]
        if name in ("td", "th"):
            for attr in ("colspan", "rowspan"):
                if re.fullmatch(r"[1-9]\d{0,2}", str(node.get(attr, ""))): attrs[attr] = node[attr]
            if node.get("scope") in ("row", "col", "rowgroup", "colgroup"): attrs["scope"] = node["scope"]
        if name in MATH_TAGS:
            for attr in ("display", "alttext", "mathvariant", "stretchy", "fence", "separator", "accent", "accentunder", "columnalign", "rowalign", "columnspan", "rowspan", "notation", "linethickness",
                         "displaystyle", "scriptlevel", "rowspacing", "columnspacing", "columnlines", "rowlines", "frame", "framespacing", "equalrows", "equalcolumns", "align", "width", "height", "depth", "voffset", "lspace", "rspace", "minsize", "maxsize", "bevelled", "movablelimits", "largeop", "symmetric", "open", "close", "separators"):
                if node.get(attr): attrs[attr] = node[attr]
            if name == "annotation" and node.get("encoding") in ("application/x-tex", "text/latex"):
                attrs["encoding"] = node["encoding"]
        if name == "a" and node.get("href"):
            href = node["href"].strip()
            joined = urljoin(base, href)
            parts = urlsplit(joined)
            target = unquote(parts.fragment)
            same_document = (parts.scheme, parts.netloc, parts.path, parts.query) == (
                urlsplit(resolved).scheme, urlsplit(resolved).netloc, urlsplit(resolved).path, "")
            if href.startswith("#") or same_document:
                if target in anchors: attrs["href"] = "#" + anchors[target]
                else: missing_refs.append({"from_dom_id": node.get("id"), "target": target})
            elif parts.scheme == "https" and parts.hostname and not parts.username and not parts.password:
                attrs.update(href=joined, rel="noreferrer noopener", referrerpolicy="no-referrer")
            else:
                removed["unsafe_link"] += 1
        if name == "img":
            source = node.get("src", "")
            url = urljoin(base, source)
            if url not in assets:
                assets[url] = load_asset(url)
            asset = assets[url]
            if asset["status"] != "ok":
                return f'<span class="missing" role="img">[Missing image: {escape(node.get("alt", source))}]</span>'
            attrs.update(src=asset["path"], alt=node.get("alt", "Original figure"))
        attr_html = "".join(f' {k}="{escape(str(v), quote=True)}"' for k, v in attrs.items())
        if name == "wbr":
            # libxml may nest subsequent text under this HTML5 void element.
            # Keep those children, but emit them AFTER a self-closing marker.
            return f"<wbr{attr_html}/>{children()}"
        if name in ("br", "hr", "img"): return f"<{name}{attr_html}>"
        return f"<{name}{attr_html}>{children()}</{name}>"

    # Render once in source order; block fragments are taken from this sanitized tree.
    safe = render(article)
    safe_soup = BeautifulSoup(safe, "lxml")
    safe_article = safe_soup.find("article")
    rendered_ids = {t["id"] for t in safe_article.find_all(id=True)}
    for link in safe_article.select('a[href^="#"]'):
        if link["href"][1:] not in rendered_ids:
            missing_refs.append({"target": link["href"], "reason": "target_not_rendered_after_sanitization"})
    candidates = [tag for tag in all_tags if role(tag)]
    selected = []
    for tag in candidates:
        # Atomic composites retain their entire rich content without duplicate blocks.
        parents = list(tag.parents)
        if any(role(p) in {"abstract", "figure", "table", "list", "equation", "footnote", "reference", "metadata", "paragraph", "title", "heading"}
               for p in parents if isinstance(p, Tag) and p is not article):
            continue
        selected.append(tag)
    # Re-render fragments independently so un-ID'd paragraphs are also retained.
    # This pass reuses asset records and does not download anything again.
    blocks = []
    for index, tag in enumerate(selected):
        seen_ids.clear()
        old_missing = len(missing_refs)
        old_removed = removed.copy()
        fragment = render(tag)
        del missing_refs[old_missing:]
        removed = old_removed
        blocks.append({
            "id": f"reading-{index}", "order": index, "kind": role(tag),
            "source_ref": {"representation": "arxiv_html", "dom_id": tag.get("id"), "dom_path": dom_path(tag)},
            "ancestor_dom_ids": [p.get("id") for p in reversed(list(tag.parents)) if isinstance(p, Tag) and p.get("id")],
            "original_text": tag.get_text(), "html": fragment,
            "asset_refs": [urljoin(base, img.get("src", "")) for img in tag.find_all("img")],
            "captions": [{"dom_id": c.get("id"), "text": c.get_text()}
                         for c in tag.find_all(["figcaption", "caption"])],
            "labels": [t.get_text() for t in tag.select(".ltx_tag")],
            "references": [a.get("href") for a in tag.find_all("a", href=True)],
            "tables": [{"dom_id": table.get("id"), "rows": [
                [{"kind": cell.name, "text": cell.get_text(),
                  "rowspan": cell.get("rowspan", "1"), "colspan": cell.get("colspan", "1")}
                 for cell in row.find_all(["td", "th"], recursive=False)]
                for row in table.find_all("tr")]} for table in ([tag] if tag.name == "table" else tag.find_all("table"))],
            "equations": [{"mathml": str(math), "tex": ([a.get_text() for a in math.find_all("annotation")
                           if a.get("encoding") in ("application/x-tex", "text/latex")]
                           or ([math["alttext"]] if math.get("alttext") else []))}
                          for math in BeautifulSoup(fragment, "lxml").find_all("math")],
        })
    inventory = Counter(role(t) for t in candidates)
    if not article.select_one(".ltx_abstract"):
        warnings.append({"kind": "abstract_not_identified"})
    warnings.extend({"kind": "html_conversion_warning", "text": t.get_text()}
                    for t in article.select(".ltx_ERROR, .ltx_warning, .ltx_missing"))
    empty = [dom_path(t) for t in candidates if not t.get_text(strip=True) and not t.find(["img", "math"])]
    title = article.find("h1")
    metadata = {}
    for node in soup.select("head meta[name][content]"):
        key = node["name"]
        if key in {"citation_title", "citation_author", "citation_date", "citation_publication_date", "citation_doi", "citation_arxiv_id"}:
            metadata.setdefault(key, []).append(node["content"])
    document = {"schema_version": 1, "parser_version": PARSER_VERSION,
                "title": title.get_text() if title else "Untitled article", "metadata": metadata, "blocks": blocks,
                "assets": list(assets.values()), "html": str(safe_article),
                "source": {"arxiv_id_version": requested, "resolved_url": resolved,
                           "representation": "arxiv_html", "sha256": digest(raw),
                           "version_evidence": evidence, "registered_pdf_match": "not_checked"}}
    diagnostics = {"source_inventory": dict(inventory), "block_inventory": dict(Counter(b["kind"] for b in blocks)),
                   "duplicate_dom_ids": [k for k, v in counts.items() if v > 1],
                   "duplicate_blocks": [text for text, n in Counter(b["original_text"] for b in blocks).items() if text.strip() and n > 1],
                   "missing_references": missing_refs, "empty_regions": empty,
                   "removed_markup": dict(removed), "warnings": warnings,
                   "source_counts": {"paragraphs": len(article.find_all("p")), "images": len(article.find_all("img")),
                                     "tables": len(article.find_all("table")), "math": len(article.find_all("math"))},
                   "coverage": "not_established; DOM inventory is not proof of PDF/full-text completeness"}
    return document, diagnostics
