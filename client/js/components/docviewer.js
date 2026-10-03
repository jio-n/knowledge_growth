// client/js/components/docviewer.js
// Block-view rendering for web/text/markdown documents (and the PDF "テキスト表示" fallback).
// Renders document.blocks per kind, attaches data-block-id/idx/page/heading-path,
// shows per-block translations, applies highlights as <mark> elements.
// IMPORTANT: source text is always inserted via textContent, never innerHTML.

import { resolveSourceAnchor } from "../anchor.js";
import { api } from "../api.js";
import { el, flashElement, normalizeWhitespace, parseAnchor } from "../util.js";
import { marked } from "/vendor/marked.esm.js";

// module-level state: only one document is rendered at a time in this SPA.
let blocksById = new Map();   // block.id -> { block, wrap, body, controlsHost }
let blocksByIdx = new Map();  // block.idx -> same record
let currentBlocks = [];
let currentVersion = {};
let currentContainer = null;
let currentSourceId = null;
let translationState = new Map(); // block.id -> { text, visible }

function tagFor(kind, level) {
  if (kind === "heading") {
    const lvl = Math.min(Math.max(level || 2, 2), 4);
    return `h${lvl}`;
  }
  if (kind === "code") return "pre";
  if (kind === "quote") return "blockquote";
  return "p";
}

function extraClassFor(kind) {
  if (kind === "list") return "block-list";
  if (kind === "table") return "block-table";
  if (kind === "figure") return "block-figure";
  return "";
}

function kindLabel(kind) {
  return { table: "表", figure: "図" }[kind] || null;
}

export function render(container, doc, sourceId) {
  currentContainer = container;
  currentSourceId = sourceId;
  blocksById = new Map();
  blocksByIdx = new Map();
  translationState = new Map();
  currentBlocks = doc.blocks || [];
  currentVersion = doc.version || {};
  container.textContent = "";
  container.classList.add("doc-scroll");

  const translations = doc.translations || {};

  for (const block of currentBlocks) {
    const wrap = el("div", {
      class: "block-wrap",
      dataset: {
        blockId: block.id,
        blockIdx: String(block.idx),
        page: block.page != null ? String(block.page) : "",
        headingPath: block.heading_path || "",
      },
    });

    const tag = tagFor(block.kind, block.level);
    const extraClass = extraClassFor(block.kind);
    const bodyEl = el(tag, {
      class: `block-body${extraClass ? ` ${extraClass}` : ""}`,
      dataset: {
        blockId: block.id,
        blockIdx: String(block.idx),
        page: block.page != null ? String(block.page) : "",
        headingPath: block.heading_path || "",
      },
    });
    const label = kindLabel(block.kind);
    if (label) bodyEl.append(el("span", { class: "block-kind-label" }, label));
    // textContent-only insertion of source document text (XSS rule, ui_spec.md).
    bodyEl.append(document.createTextNode(block.text || ""));
    wrap.append(bodyEl);

    const controlsHost = el("div", { class: "block-controls" });
    wrap.append(controlsHost);

    const translationHost = el("div", { class: "translation-host" });
    wrap.append(translationHost);

    container.append(wrap);

    const record = { block, wrap, body: bodyEl, controlsHost, translationHost };
    blocksById.set(block.id, record);
    blocksByIdx.set(block.idx, record);

    const existing = translations[block.id];
    translationState.set(block.id, { text: existing || null, visible: !!existing });
    renderBlockControls(record);
    renderTranslationHost(record);
  }

  for (const h of doc.highlights || []) {
    applyHighlightMark(h);
  }
}

function renderBlockControls(record) {
  record.controlsHost.textContent = "";
  if (!record.block.text) return;
  const st = translationState.get(record.block.id);
  if (!st || !st.visible) {
    record.controlsHost.append(el("button", {
      class: "block-translate-btn",
      type: "button",
      title: "この段落を翻訳",
      onClick: () => translateBlock(record),
    }, "訳"));
  }
}

async function translateBlock(record) {
  const btn = record.controlsHost.querySelector(".block-translate-btn");
  if (btn) { btn.disabled = true; btn.textContent = "..."; }
  try {
    const res = await api.translate({ source_id: currentSourceId, text: record.block.text, block_id: record.block.id });
    setTranslation(record.block.id, res.translation, true);
  } catch {
    if (btn) { btn.disabled = false; btn.textContent = "訳"; }
  }
}

/** Show (and cache) a translation for a block. Used by hover-button and by selection.js
 *  (block-covering selection translate action, per source_anchor/ui_spec §selection). */
export function setTranslation(blockId, text, visible = true) {
  translationState.set(blockId, { text, visible });
  const record = blocksById.get(blockId);
  if (record) {
    renderTranslationHost(record);
    renderBlockControls(record);
  }
}

function renderTranslationHost(record) {
  record.translationHost.textContent = "";
  const st = translationState.get(record.block.id);
  if (!st || !st.visible || !st.text) return;
  const box = el("div", { class: "translation ai-content" });
  box.innerHTML = marked.parse(st.text || "");
  box.append(el("button", {
    class: "translation__hide",
    type: "button",
    onClick: () => {
      translationState.set(record.block.id, { ...st, visible: false });
      renderTranslationHost(record);
      renderBlockControls(record);
    },
  }, "訳を隠す"));
  record.translationHost.append(box);
}

// ---------- highlights ----------

export function applyHighlightMark(highlight) {
  const anchor = parseAnchor(highlight.anchor);
  const result = resolveSourceAnchor(anchor, currentBlocks, currentVersion);
  const block = result.status === "resolved" ? result.block : null;
  if (!block) return;
  const record = blocksById.get(block.id);
  if (!record) return;
  const quote = anchor?.quote;
  if (!quote) return;
  const idx = block.text.indexOf(quote);
  if (idx === -1 || block.text.indexOf(quote, idx + 1) !== -1) return;
  wrapRangeApply(record.body, idx, idx + quote.length, (mark) => {
    mark.className = "kg-highlight";
    mark.dataset.highlightId = highlight.id;
    mark.title = "クリックで削除";
    mark.addEventListener("click", (e) => {
      e.stopPropagation();
      showHighlightMenu(mark, highlight.id);
    });
  });
}

export function removeHighlightMark(highlightId) {
  const marks = currentContainer?.querySelectorAll(`mark.kg-highlight[data-highlight-id="${highlightId}"]`) || [];
  marks.forEach((m) => {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    parent.removeChild(m);
    parent.normalize();
  });
}

let openMenu = null;
function showHighlightMenu(markEl, highlightId) {
  if (openMenu) { openMenu.remove(); openMenu = null; }
  const rect = markEl.getBoundingClientRect();
  const menu = el("div", { class: "highlight-menu", style: { left: `${rect.left}px`, top: `${rect.bottom + 4}px` } },
    el("button", {
      class: "btn btn--sm btn--danger",
      type: "button",
      onClick: async () => {
        menu.remove();
        openMenu = null;
        try {
          await api.deleteHighlight(highlightId);
          removeHighlightMark(highlightId);
        } catch { /* toasted by api.js */ }
      },
    }, "ハイライト削除"),
  );
  document.body.append(menu);
  openMenu = menu;
  setTimeout(() => {
    document.addEventListener("click", function onDoc(e) {
      if (!menu.contains(e.target)) { menu.remove(); openMenu = null; document.removeEventListener("click", onDoc); }
    });
  }, 0);
}

/** Wrap the [start, end) character range (against the block's plain text) in a new element,
 *  running `configure(mark)` on the (not-yet-inserted) mark element before it is spliced in. */
function wrapRangeApply(root, start, end, configure) {
  try {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node, acc = 0;
    let startNode, startOffset, endNode, endOffset;
    while ((node = walker.nextNode())) {
      const len = node.textContent.length;
      if (startNode === undefined && acc + len >= start) { startNode = node; startOffset = start - acc; }
      if (endNode === undefined && acc + len >= end) { endNode = node; endOffset = end - acc; }
      acc += len;
      if (startNode !== undefined && endNode !== undefined) break;
    }
    if (startNode === undefined || endNode === undefined) return null;
    const range = document.createRange();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset);
    const mark = document.createElement("mark");
    configure(mark);
    range.surroundContents(mark);
    return mark;
  } catch {
    return null; // overlapping ranges etc. - fail silently, non-critical UI sugar
  }
}

// ---------- scroll / flash (used by resolveAnchor in reader.js) ----------

export function scrollToBlockId(blockId) {
  const record = blocksById.get(blockId);
  if (!record) return false;
  record.wrap.scrollIntoView({ behavior: "smooth", block: "center" });
  flashElement(record.wrap);
  return true;
}

export function scrollToBlockIdx(idx) {
  const record = blocksByIdx.get(idx);
  if (!record) return false;
  record.wrap.scrollIntoView({ behavior: "smooth", block: "center" });
  flashElement(record.wrap);
  return true;
}

// ---------- in-document search ----------

export function findTextMatches(query) {
  const q = normalizeWhitespace(query).toLowerCase();
  if (!q) return [];
  return currentBlocks
    .filter((b) => normalizeWhitespace(b.text || "").toLowerCase().includes(q))
    .map((b) => b.idx);
}

export function markSearchMatches(idxList, activeIdx) {
  for (const record of blocksByIdx.values()) {
    record.wrap.classList.remove("search-hit", "search-hit-active");
  }
  for (const idx of idxList) {
    const record = blocksByIdx.get(idx);
    if (record) record.wrap.classList.add(idx === activeIdx ? "search-hit-active" : "search-hit");
  }
}

export function clearSearchMatches() {
  for (const record of blocksByIdx.values()) {
    record.wrap.classList.remove("search-hit", "search-hit-active");
  }
}

export function jumpToBlockIdx(idx) {
  const record = blocksByIdx.get(idx);
  if (!record) return;
  record.wrap.scrollIntoView({ behavior: "smooth", block: "center" });
}
