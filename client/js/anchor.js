// Pure SourceAnchor resolver shared by navigation and persisted highlights.
// Never choose the first of equally plausible evidence candidates.
const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();

export function geometry(value) {
  try {
    const g = typeof value === "string" ? JSON.parse(value) : value;
    const validRect = (r) => Array.isArray(r) && r.length === 4 && r.every(Number.isFinite)
      && r[2] > r[0] && r[3] > r[1];
    if (!g || g.coordinate_system !== "pymupdf_unrotated" || g.units !== "pt"
        || !validRect(g.rect) || !validRect(g.page_rect)) return null;
    if (g.rect[0] < g.page_rect[0] || g.rect[1] < g.page_rect[1]
        || g.rect[2] > g.page_rect[2] || g.rect[3] > g.page_rect[3]) return null;
    return g;
  } catch { return null; }
}

function overlap(a, b) {
  const x = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0]));
  const y = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const area = (r) => (r[2] - r[0]) * (r[3] - r[1]);
  return x * y / Math.min(area(a), area(b));
}

function occurrences(block, anchor) {
  const text = norm(block.text), quote = norm(anchor.quote);
  if (!quote) return [];
  const prefix = norm(anchor.prefix), suffix = norm(anchor.suffix);
  const hits = [];
  for (let from = 0; from <= text.length - quote.length;) {
    const index = text.indexOf(quote, from);
    if (index < 0) break;
    if ((!prefix || text.slice(0, index).trimEnd().endsWith(prefix))
        && (!suffix || text.slice(index + quote.length).trimStart().startsWith(suffix))) hits.push(index);
    from = index + 1;
  }
  return hits;
}

export function resolveSourceAnchor(raw, blocks, version = {}) {
  let anchor;
  try { anchor = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { /* unresolved */ }
  const fail = (candidates = [], reason = "no_match") => ({
    status: candidates.length ? "candidates" : "unresolved", method: null,
    block: null, page: null, candidates: [...new Set(candidates)], reason,
  });
  if (!anchor || typeof anchor !== "object" || Array.isArray(anchor)) return fail();
  const sameVersion = !!anchor.sourceVersion && anchor.sourceVersion === version.id;
  const sameSource = !!anchor.sourceHash && anchor.sourceHash === version.content_hash;
  const quote = norm(anchor.quote);
  const resolved = (block, method) => ({ status: "resolved", method, block,
    page: block.page, candidates: [], reason: null });
  const hasQuote = (b) => !quote || norm(b.text).includes(quote);
  // Old anchors without version use portable selectors, never bare ID trust.
  if (sameVersion && anchor.blockId) {
    const block = blocks.find((b) => b.id === anchor.blockId && b.version_id === version.id);
    if (block && hasQuote(block)) return resolved(block, "block_id");
  }
  let uncertain = [];
  const box = geometry(anchor.bbox);
  if (box && Number.isInteger(anchor.page) && anchor.page > 0) {
    const hits = blocks.filter((b) => {
      const g = geometry(b.bbox || b.bbox_json);
      return b.page === anchor.page && g
        && g.page_rect.every((v, i) => Math.abs(v - box.page_rect[i]) < .01)
        && overlap(g.rect, box.rect) >= .8;
    });
    const supported = hits.filter((b) => !quote || occurrences(b, anchor).length === 1);
    // Geometry on changed source bytes cannot verify content by itself.
    const portableMatches = quote ? blocks.flatMap((b) => occurrences(b, anchor).map(() => b)) : [];
    if (supported.length === 1 && (sameVersion || sameSource || portableMatches.length === 1))
      return resolved(supported[0], "bbox");
    uncertain = supported.length ? supported : hits;
  }
  if (quote) {
    const matches = blocks.flatMap((block) => occurrences(block, anchor).map(() => block));
    if (matches.length === 1) return resolved(matches[0], "quote");
    if (matches.length > 1) return fail(matches, "ambiguous_quote");
    // Prefix/suffix disagreement is evidence against silently using idx/page.
    if (blocks.some(hasQuote)) return fail(blocks.filter(hasQuote), "context_changed");
  }
  if (uncertain.length) return fail(uncertain, "ambiguous_bbox");
  // Index is positional, so it is only authoritative in the original version.
  if (sameVersion && Number.isInteger(anchor.blockIdx)) {
    const block = blocks.find((b) => b.idx === anchor.blockIdx);
    if (block && hasQuote(block)) return resolved(block, "block_idx");
  }
  if (Number.isInteger(anchor.page) && anchor.page > 0 && blocks.some((b) => b.page === anchor.page)) {
    return { ...fail(), status: "page_only", method: "page", page: anchor.page,
      reason: "evidence_unresolved" };
  }
  return fail();
}
