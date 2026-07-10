// client/js/views/reader.js
// 画面2: 読解画面 (#/read/<id>) - pane skeleton, tabs, source anchor resolution,
// prev/next source navigation. Only one reader is ever mounted at a time in this SPA,
// so module-level state below is scoped to "the current reader".

import { api } from "../api.js";
import { el, toast, debounce, normalizeWhitespace, parseAnchor } from "../util.js";
import {
  state, loadReaderUi, saveReaderUi, neighborSourceId, setSourcesCache,
  consumePendingFocus, READING_STATUS_LABEL,
} from "../state.js";
import * as docviewer from "../components/docviewer.js";
import * as pdfviewer from "../components/pdfviewer.js";
import * as selection from "../components/selection.js";
import * as qa from "../components/qa.js";
import * as summary from "../components/summary.js";
import * as note from "../components/note.js";

let readerState = null; // { sourceId, source, doc, viewMode, activeTab, ui, els }
let searchState = { query: "", matches: [], idx: -1 };
let activeDrag = null; // pane-resize drag context (module-level: see wireDivider/mousemove below)

export async function render(container, sourceId, isCurrent = () => true) {
  container.textContent = "";
  readerState = {
    sourceId, source: null, doc: null, viewMode: "block", activeTab: "summary",
    ui: loadReaderUi(sourceId), els: null,
  };
  searchState = { query: "", matches: [], idx: -1 };

  if (!state.sourcesCache.length) {
    try {
      const libRes = await api.listSources({}, { silent: true });
      if (!isCurrent()) return;
      setSourcesCache(libRes.sources || []);
    } catch { /* prev/next nav degrades gracefully */ }
  }
  if (!isCurrent()) return; // superseded by another navigation

  let source, doc;
  try {
    const [sourceRes, docRes] = await Promise.all([api.getSource(sourceId), api.getDocument(sourceId)]);
    source = sourceRes.source;
    doc = docRes;
  } catch {
    if (isCurrent()) container.append(el("div", { class: "library__empty" }, "資料の読み込みに失敗しました。"));
    return;
  }
  // navigated away while loading (either to another source, or to a different route entirely)
  if (!isCurrent() || !readerState || readerState.sourceId !== sourceId) return;

  readerState.source = source;
  readerState.doc = doc;
  readerState.viewMode = source.type === "pdf" ? (readerState.ui.pdfMode || "pdf") : "block";
  readerState.activeTab = readerState.ui.activeTab || "summary";

  buildLayout(container);
  selection.init(readerState.els.leftPane, {
    sourceId: readerState.sourceId,
    getBlocks: () => (readerState ? readerState.doc.blocks : []),
    switchTab,
  });
  restoreScroll();

  const focusOpts = consumePendingFocusOpts();
  switchTab(readerState.activeTab, focusOpts);
}

export function unmount() {
  summary.unmount();
  qa.unmount();
  note.unmount();
  pdfviewer.unmount();
  activeDrag = null;
  readerState = null;
}

function consumePendingFocusOpts() {
  const f = consumePendingFocus(readerState.sourceId);
  if (!f) return {};
  if (f.kind === "knowledge") {
    readerState.activeTab = "note";
    return { focusItemId: f.refId };
  }
  if (f.kind === "question" || f.kind === "answer") {
    readerState.activeTab = "qa";
    return { focusQuestionId: f.refId };
  }
  return {};
}

// ---------- layout ----------

function buildLayout(container) {
  const source = readerState.source;

  const header = buildHeader(source);

  const searchInput = el("input", {
    type: "search",
    placeholder: "文書内検索",
    onInput: debounce((e) => runSearch(e.target.value), 250),
    onKeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); searchNext(); } },
  });
  const searchCount = el("span", { class: "doc-toolbar__search-count" }, "");
  const searchNextBtn = el("button", { class: "btn btn--sm", type: "button", onClick: () => searchNext() }, "次へ");

  const blockContainer = el("div", { class: "doc-scroll" });
  const pdfContainer = el("div", { class: "pdf-scroll", hidden: true });

  const toolbarChildren = [];
  let toggleButtons = null;
  if (source.type === "pdf") {
    const pdfToggleBtn = el("button", { type: "button" }, "PDF表示");
    const textToggleBtn = el("button", { type: "button" }, "テキスト表示");
    pdfToggleBtn.addEventListener("click", () => setViewMode("pdf"));
    textToggleBtn.addEventListener("click", () => setViewMode("block"));
    toggleButtons = { pdf: pdfToggleBtn, block: textToggleBtn };
    toolbarChildren.push(el("div", { class: "doc-toolbar__toggle" }, pdfToggleBtn, textToggleBtn));
  }
  toolbarChildren.push(el("div", { class: "doc-toolbar__search" }, searchInput, searchNextBtn, searchCount));
  const toolbar = el("div", { class: "doc-toolbar" }, ...toolbarChildren);

  const leftPane = el("div", { class: "reader-pane-left" }, toolbar, blockContainer, pdfContainer);
  const divider = el("div", { class: "reader-divider" });

  const tabDefs = [["summary", "概要"], ["qa", "対話"], ["note", "ノート"]];
  const tabsHeader = el("div", { class: "tabs-header" },
    ...tabDefs.map(([key, label]) => el("button", {
      type: "button", dataset: { tab: key }, onClick: () => switchTab(key),
    }, label)),
  );
  const summaryPanel = el("div", { class: "tab-panel", dataset: { tabPanel: "summary" } });
  const qaPanel = el("div", { class: "tab-panel", dataset: { tabPanel: "qa" }, hidden: true });
  const notePanel = el("div", { class: "tab-panel", dataset: { tabPanel: "note" }, hidden: true });
  const rightPane = el("div", { class: "reader-pane-right" }, tabsHeader, summaryPanel, qaPanel, notePanel);

  const split = el("div", { class: "reader-split" }, leftPane, divider, rightPane);
  container.append(el("div", { class: "reader" }, header, split));

  readerState.els = {
    leftPane, rightPane, blockContainer, pdfContainer, divider, split,
    tabsHeader, summaryPanel, qaPanel, notePanel, searchInput, searchCount, toggleButtons,
  };

  applyPaneWidth();
  wireDivider();

  docviewer.render(blockContainer, readerState.doc, source.id);
  blockContainer.hidden = readerState.viewMode !== "block";
  pdfContainer.hidden = readerState.viewMode !== "pdf";
  if (toggleButtons) {
    toggleButtons.pdf.classList.toggle("active", readerState.viewMode === "pdf");
    toggleButtons.block.classList.toggle("active", readerState.viewMode === "block");
  }
  if (source.type === "pdf") {
    pdfviewer.mount(pdfContainer, { fileUrl: api.fileUrl(source.id) });
  }
}

function buildHeader(source) {
  const backBtn = el("a", { href: "#/", class: "btn btn--sm btn--ghost" }, "← ライブラリ");
  const titleEl = el("div", { class: "reader-header__title" }, source.title || "(無題)");
  const statusSelect = el("select", {
    onChange: async (e) => {
      const prev = source.reading_status;
      source.reading_status = e.target.value;
      try {
        await api.patchSource(source.id, { reading_status: e.target.value });
      } catch {
        source.reading_status = prev;
        e.target.value = prev;
      }
    },
  }, ...Object.entries(READING_STATUS_LABEL).map(([k, v]) =>
    el("option", { value: k, selected: k === source.reading_status || undefined }, v)));

  const prevId = neighborSourceId(source.id, "prev");
  const nextId = neighborSourceId(source.id, "next");
  const prevBtn = el("button", {
    class: "btn btn--sm", type: "button", disabled: !prevId,
    onClick: () => { if (prevId) location.hash = `#/read/${prevId}`; },
  }, "前の資料");
  const nextBtn = el("button", {
    class: "btn btn--sm", type: "button", disabled: !nextId,
    onClick: () => { if (nextId) location.hash = `#/read/${nextId}`; },
  }, "次の資料");
  const mdBtn = el("a", { class: "btn btn--sm", href: api.exportMdUrl(source.id, 1), download: true }, "MD出力");

  return el("div", { class: "reader-header" },
    backBtn, titleEl, statusSelect,
    el("div", { class: "reader-header__nav" }, prevBtn, nextBtn),
    mdBtn,
  );
}

function setViewMode(mode) {
  if (!readerState) return;
  readerState.viewMode = mode;
  readerState.els.blockContainer.hidden = mode !== "block";
  readerState.els.pdfContainer.hidden = mode !== "pdf";
  if (readerState.els.toggleButtons) {
    readerState.els.toggleButtons.pdf.classList.toggle("active", mode === "pdf");
    readerState.els.toggleButtons.block.classList.toggle("active", mode === "block");
  }
  saveReaderUi(readerState.sourceId, { pdfMode: mode });
}

// ---------- tabs ----------

function switchTab(tabKey, opts = {}) {
  if (!readerState) return;
  readerState.activeTab = tabKey;
  saveReaderUi(readerState.sourceId, { activeTab: tabKey });

  for (const btn of readerState.els.tabsHeader.querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.tab === tabKey);
  }
  readerState.els.summaryPanel.hidden = tabKey !== "summary";
  readerState.els.qaPanel.hidden = tabKey !== "qa";
  readerState.els.notePanel.hidden = tabKey !== "note";

  summary.unmount();
  qa.unmount();
  note.unmount();

  if (tabKey === "summary") {
    summary.mount(readerState.els.summaryPanel, readerState.sourceId);
  } else if (tabKey === "qa") {
    qa.mount(readerState.els.qaPanel, readerState.sourceId, {
      resolveAnchor, switchTab, focusQuestionId: opts.focusQuestionId,
    });
  } else if (tabKey === "note") {
    note.mount(readerState.els.notePanel, readerState.sourceId, {
      resolveAnchor, switchTab, focusItemId: opts.flashId || opts.focusItemId,
    });
  }
}

// ---------- pane resize (module-level drag listeners registered once) ----------

document.addEventListener("mousemove", (e) => {
  if (!activeDrag) return;
  const rect = activeDrag.splitEl.getBoundingClientRect();
  let pct = ((e.clientX - rect.left) / rect.width) * 100;
  pct = Math.min(80, Math.max(20, pct));
  activeDrag.pane.style.flex = `0 0 ${pct}%`;
  activeDrag.pct = pct;
});
document.addEventListener("mouseup", () => {
  if (!activeDrag) return;
  activeDrag.divider.classList.remove("dragging");
  saveReaderUi(activeDrag.sourceId, { paneWidthPct: activeDrag.pct });
  activeDrag = null;
});

function applyPaneWidth() {
  const pct = readerState.ui.paneWidthPct || 55;
  readerState.els.leftPane.style.flex = `0 0 ${pct}%`;
}

function wireDivider() {
  const { divider, leftPane, split } = readerState.els;
  divider.addEventListener("mousedown", (e) => {
    e.preventDefault();
    divider.classList.add("dragging");
    activeDrag = {
      divider, pane: leftPane, splitEl: split,
      sourceId: readerState.sourceId, pct: readerState.ui.paneWidthPct || 55,
    };
  });
}

// ---------- scroll persistence ----------

function restoreScroll() {
  const ui = readerState.ui;
  const { blockContainer, pdfContainer } = readerState.els;
  if (ui.scrollTop) {
    const target = readerState.viewMode === "pdf" ? pdfContainer : blockContainer;
    setTimeout(() => { target.scrollTop = ui.scrollTop; }, 300);
  }
  const onScroll = debounce(() => {
    if (!readerState) return;
    const active = readerState.viewMode === "pdf" ? pdfContainer : blockContainer;
    saveReaderUi(readerState.sourceId, { scrollTop: active.scrollTop });
  }, 400);
  blockContainer.addEventListener("scroll", onScroll);
  pdfContainer.addEventListener("scroll", onScroll);
}

// ---------- in-document search ----------

function runSearch(query) {
  searchState = { query, matches: docviewer.findTextMatches(query), idx: -1 };
  if (readerState.viewMode === "block") docviewer.markSearchMatches(searchState.matches, -1);
  updateSearchCount();
}

function searchNext() {
  const q = readerState.els.searchInput.value.trim();
  if (q !== searchState.query) runSearch(q);
  if (!searchState.matches.length) { updateSearchCount(); return; }
  searchState.idx = (searchState.idx + 1) % searchState.matches.length;
  const blockIdx = searchState.matches[searchState.idx];
  const block = readerState.doc.blocks.find((b) => b.idx === blockIdx);
  if (readerState.viewMode === "block") {
    docviewer.markSearchMatches(searchState.matches, blockIdx);
    docviewer.jumpToBlockIdx(blockIdx);
  } else if (readerState.viewMode === "pdf" && block && block.page) {
    pdfviewer.scrollToPage(block.page, block.text.slice(0, 80));
  }
  updateSearchCount();
}

function updateSearchCount() {
  const el_ = readerState.els.searchCount;
  if (!searchState.query) { el_.textContent = ""; return; }
  el_.textContent = searchState.matches.length
    ? `${searchState.idx + 1}/${searchState.matches.length}` : "0/0";
}

// ---------- anchor resolution (source_anchor_spec.md §解決) ----------

export function resolveAnchor(rawAnchor) {
  if (!readerState) return;
  const anchor = parseAnchor(rawAnchor); // defensive: anchor may arrive pre-parsed or as a JSON string
  if (!anchor) {
    toast("原文位置を特定できませんでした(資料が更新された可能性)");
    return;
  }
  const blocks = readerState.doc.blocks || [];
  const norm = normalizeWhitespace;
  let target = null;

  // 1. blockId
  if (anchor.blockId) {
    target = blocks.find((b) => b.id === anchor.blockId) || null;
  }
  // 2. blockIdx (+ quote 先頭80字 contained)
  if (!target && anchor.blockIdx != null) {
    const b = blocks.find((bb) => bb.idx === anchor.blockIdx);
    if (b && (!anchor.quote || norm(b.text).includes(norm(anchor.quote).slice(0, 80)))) target = b;
  }
  // 3. full quote search, disambiguate via prefix/suffix + headingPath
  if (!target && anchor.quote) {
    const q = norm(anchor.quote).slice(0, 80);
    const candidates = blocks.filter((b) => norm(b.text).includes(q));
    if (candidates.length === 1) target = candidates[0];
    else if (candidates.length > 1) target = pickBestByContext(candidates, anchor);
  }

  if (target) {
    if (readerState.source.type === "pdf" && readerState.viewMode === "pdf" && target.page) {
      pdfviewer.scrollToPage(target.page, anchor.quote || target.text.slice(0, 80));
    } else {
      if (readerState.viewMode !== "block") setViewMode("block");
      docviewer.scrollToBlockId(target.id);
    }
    return;
  }

  // 4. page fallback (PDF)
  if (anchor.page) {
    if (readerState.source.type === "pdf") {
      if (readerState.viewMode !== "pdf") setViewMode("pdf");
      pdfviewer.scrollToPage(anchor.page, anchor.quote);
      return;
    }
    const b = blocks.find((bb) => bb.page === anchor.page);
    if (b) { docviewer.scrollToBlockId(b.id); return; }
  }

  // 5. fail
  toast("原文位置を特定できませんでした(資料が更新された可能性)");
}

function pickBestByContext(candidates, anchor) {
  const norm = normalizeWhitespace;
  const quote = norm(anchor.quote || "").slice(0, 80);
  const wantPrefix = norm(anchor.prefix || "").slice(-20);
  const wantSuffix = norm(anchor.suffix || "").slice(0, 20);
  let best = candidates[0];
  let bestScore = -1;
  for (const b of candidates) {
    const text = norm(b.text);
    const idx = text.indexOf(quote);
    let score = 0;
    if (idx >= 0) {
      const actualPrefix = text.slice(Math.max(0, idx - 60), idx);
      const actualSuffix = text.slice(idx + quote.length, idx + quote.length + 60);
      if (wantPrefix && actualPrefix.endsWith(wantPrefix)) score += 2;
      if (wantSuffix && actualSuffix.startsWith(wantSuffix)) score += 2;
    }
    if (anchor.headingPath && b.heading_path === anchor.headingPath) score += 1;
    if (score > bestScore) { bestScore = score; best = b; }
  }
  return best;
}
