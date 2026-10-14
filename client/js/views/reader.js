// client/js/views/reader.js
// 画面2: 読解画面 (#/read/<id>) - pane skeleton, tabs, source anchor resolution,
// prev/next source navigation. Only one reader is ever mounted at a time in this SPA,
// so module-level state below is scoped to "the current reader".

import { resolveSourceAnchor } from "../anchor.js";
import { api } from "../api.js";
import { el, toast, debounce, parseAnchor } from "../util.js";
import {
  state, loadReaderUi, saveReaderUi, neighborSourceId, setSourcesCache,
  consumePendingFocus, READING_STATUS_LABEL,
} from "../state.js";
import * as docviewer from "../components/docviewer.js";
import * as pdfviewer from "../components/pdfviewer.js";
import * as selection from "../components/selection.js";
import * as qa from "../components/qa.js";
import * as summary from "../components/summary.js";
import * as paperbrief from "../components/paperbrief.js";
import { openImportBridge } from "../components/importbridge.js";
import { mapPosition, originalSelection } from "../pdfalignment.js";
import { createJapanesePdfControls } from "../components/japanesepdf.js";
import * as note from "../components/note.js";

let readerState = null; // { sourceId, source, doc, viewMode, activeTab, ui, els }
let searchState = { query: "", matches: [], idx: -1 };
let searchRevision = 0;
let activeDrag = null; // pane-resize drag context (module-level: see wireDivider/mousemove below)

export async function render(container, sourceId, isCurrent = () => true) {
  container.textContent = "";
  readerState = {
    sourceId, source: null, doc: null, viewMode: "block", activeTab: "brief",
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
  const routeParams = new URLSearchParams(location.hash.split("?")[1] || "");
  readerState.activeTab = routeParams.get("tab") === "brief" ? "brief" : (readerState.ui.activeTab || "brief");

  buildLayout(container);
  selection.init(readerState.els.leftPane, {
    sourceId: readerState.sourceId,
    getBlocks: () => (readerState ? readerState.doc.blocks : []),
    getVersion: () => readerState?.doc.version,
    switchTab,
    isJapanese: () => readerState?.pdfLanguage === "japanese" && readerState?.viewMode === "pdf",
    onOriginal: (page, bbox) => showOriginalSelection(page, bbox),
  });
  restoreScroll();

  const focusOpts = consumePendingFocusOpts();
  switchTab(readerState.activeTab, focusOpts);
  // Import preview evidence opens in a separate Reader so the wizard remains intact.
  const evidence = routeParams.get("evidence");
  if (evidence) {
    try {
      const request = JSON.parse(evidence);
      const mounted = readerState;
      await mounted.pdfReady;
      if (isCurrent() && readerState === mounted) navigateBriefEvidence(request.anchor, request.kind);
    } catch { toast("Evidence参照を開けませんでした。"); }
  }
}

export function unmount() {
  persistReaderPosition();
  readerState?.japaneseControls?.dispose();
  paperbrief.unmount();
  summary.unmount();
  qa.unmount();
  note.unmount();
  selection.unmount();
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

  const panelToggle = el("button", { class: "btn btn--sm", type: "button",
    onClick: () => toggleSidePanel() }, "PDFを全幅で読む");
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
  let pdfControls = null;
  if (source.type === "pdf") {
    const zoomInput = el("input", { type: "number", min: "75", max: "250", step: "5",
      "aria-label": "PDF倍率（%）", title: "75〜250%を入力。幅・ページに合わせる場合は自動計算。",
      onChange: (e) => { e.target.value = Math.round(pdfviewer.setZoom(Number(e.target.value) / 100) * 100); },
      onKeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } },
    });
    const widthButton = el("button", { type: "button", class: "btn btn--sm",
      onClick: () => pdfviewer.fit("width") }, "幅に合わせる");
    const pageButton = el("button", { type: "button", class: "btn btn--sm",
      onClick: () => pdfviewer.fit("page") }, "ページ全体");
    const pageInput = el("input", { type: "number", min: "1", "aria-label": "PDFページ番号",
      onChange: (e) => {
        const num = Math.min(pdfviewer.pageCount(), Math.max(1, Math.floor(Number(e.target.value) || 1)));
        e.target.value = num;
        pdfviewer.scrollToPage(num);
      },
      onKeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); } },
    });
    const pageTotal = el("span", {}, "/ …");
    pdfControls = el("div", { class: "pdf-controls", role: "group", "aria-label": "PDF表示設定" },
      el("button", { type: "button", class: "btn btn--sm", "aria-label": "PDFを縮小",
        onClick: () => pdfviewer.setZoom((Number(zoomInput.value) - 25) / 100) }, "−"),
      zoomInput, el("span", {}, "%"),
      el("button", { type: "button", class: "btn btn--sm", "aria-label": "PDFを拡大",
        onClick: () => pdfviewer.setZoom((Number(zoomInput.value) + 25) / 100) }, "+"),
      widthButton, pageButton, pageInput, pageTotal);
    pdfControls.update = (view) => {
      if (document.activeElement !== zoomInput) zoomInput.value = view.percent;
      if (document.activeElement !== pageInput) pageInput.value = view.position?.page || 1;
      pageInput.max = view.pages;
      pageTotal.textContent = `/ ${view.pages}`;
      widthButton.setAttribute("aria-pressed", String(view.mode === "width"));
      pageButton.setAttribute("aria-pressed", String(view.mode === "page"));
    };
    toolbarChildren.push(pdfControls, panelToggle);
  }
  toolbarChildren.push(el("div", { class: "doc-toolbar__search" }, searchInput, searchNextBtn, searchCount));
  const toolbar = el("div", { class: "doc-toolbar" }, ...toolbarChildren);

  const mountedReader = readerState;
  const japaneseControls = source.type === "pdf" ? createJapanesePdfControls(source.id, {
    rememberedId: readerState.ui.japaneseGenerationId,
    onSwitch: async (language, artifact, blockId) => {
      if (readerState !== mountedReader) return;
      await switchPdfLanguage(language, artifact);
      if (readerState !== mountedReader) return;
      if (blockId) resolveAnchor({ blockId, sourceVersion: mountedReader.doc.version.id });
    },
  }) : null;
  readerState.japaneseControls = japaneseControls;
  const leftPane = el("div", { class: "reader-pane-left" }, toolbar,
    ...(japaneseControls ? [japaneseControls.root] : []), blockContainer, pdfContainer);
  const divider = el("div", { class: "reader-divider" });

  const tabDefs = [["brief", "Paper Brief"], ["summary", "概要"], ["qa", "対話"], ["note", "ノート"]];
  const tabsHeader = el("div", { class: "tabs-header" },
    ...tabDefs.map(([key, label]) => el("button", {
      type: "button", dataset: { tab: key }, onClick: () => switchTab(key),
    }, label)),
  );
  const briefPanel = el("div", { class: "tab-panel", dataset: { tabPanel: "brief" } });
  const summaryPanel = el("div", { class: "tab-panel", dataset: { tabPanel: "summary" } });
  const qaPanel = el("div", { class: "tab-panel", dataset: { tabPanel: "qa" }, hidden: true });
  const notePanel = el("div", { class: "tab-panel", dataset: { tabPanel: "note" }, hidden: true });
  const rightPane = el("div", { class: "reader-pane-right" }, tabsHeader, briefPanel, summaryPanel, qaPanel, notePanel);

  const split = el("div", { class: "reader-split" }, leftPane, divider, rightPane);
  container.append(el("div", { class: "reader" }, header, split));

  readerState.els = {
    leftPane, rightPane, blockContainer, pdfContainer, divider, split,
    tabsHeader, briefPanel, summaryPanel, qaPanel, notePanel, searchInput, searchCount, toggleButtons, pdfControls, panelToggle,
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
    const mounted = readerState;
    pdfControls.hidden = mounted.viewMode !== "pdf";
    mounted.pdfLanguage = "original";
    mounted.pdfReady = mountPdf(mounted, api.fileUrl(source.id), mounted.ui.pdfViews?.original || (mounted.ui.pdfLanguage === "japanese" ? {} : mounted.ui.pdfView) || { scrollTop: mounted.ui.scrollTop });
    mounted.japaneseControls.ready.then(async () => {
      if (readerState !== mounted || mounted.pdfLanguageTouched || mounted.ui.pdfLanguage !== "japanese" || new URLSearchParams(location.hash.split("?")[1] || "").has("evidence")) return;
      const artifact = mounted.japaneseControls.getArtifact();
      if (artifact?.state === "preview_ready") {
        const mode = mounted.viewMode;
        await switchPdfLanguage("japanese", artifact, true);
        if (readerState === mounted && mode === "block") setViewMode("block");
      }
    });
  }
}

function mountPdf(mounted, fileUrl, view) {
  const viewKey=mounted.pdfLanguage === "japanese" ? "japanese:"+mounted.pdfArtifact.generation_id : "original";
  return pdfviewer.mount(mounted.els.pdfContainer, {
    fileUrl, view,
    onChange: (next) => {
      if (readerState !== mounted) return;
      mounted.els.pdfControls.update(next);
      const saved={ mode:next.mode, zoom:next.zoom, position:next.position };
      saveReaderUi(mounted.sourceId, { pdfView:saved,
        pdfViews:{...loadReaderUi(mounted.sourceId).pdfViews,[viewKey]:saved} });
    },
  });
}

async function switchPdfLanguage(language, artifact = null, restore = false) {
  const mounted = readerState;
  if (!mounted || mounted.source.type !== "pdf") return;
  mounted.pdfLanguageTouched = true;
  setViewMode("pdf");
  const token = language === "japanese" ? artifact?.generation_id : null;
  if (language === mounted.pdfLanguage && token === (mounted.pdfArtifact?.generation_id || null)) {
    await mounted.pdfReady; return;
  }
  pdfviewer.rememberPosition();
  const ui=loadReaderUi(mounted.sourceId);
  let view=ui.pdfView || mounted.ui.pdfView || {};
  const report=language === "japanese" ? artifact?.report : mounted.pdfArtifact?.report;
  if (restore && ui.pdfViews?.["japanese:"+token]) view=ui.pdfViews["japanese:"+token];
  else if (mounted.pdfLanguage !== language) {
    const position=mapPosition(report,view.position,language === "japanese" ? "toJapanese" : "toOriginal");
    view={...view,position:position || ui.pdfViews?.[language === "original" ? "original" : "japanese:"+token]?.position};
    if (language === "japanese" && report?.layout_version && !report.selected_pages.includes(ui.pdfView?.position?.page))
      toast("この原文ページは試作対象外です。近い対象箇所の日本語を表示しました。");
  }
  // mount unmounts the sole previous controller synchronously, then loads anew.
  searchRevision++;
  searchState={query:"",matches:[],idx:-1};
  mounted.pdfLanguage = language;
  mounted.pdfArtifact = language === "japanese" ? artifact : null;
  mounted.japaneseControls.setLanguage(language);
  saveReaderUi(mounted.sourceId, { pdfLanguage: language, japaneseGenerationId: token });
  const loading = mountPdf(mounted, token ? api.japanesePdfFileUrl(mounted.sourceId, token) : api.fileUrl(mounted.sourceId), view);
  mounted.pdfReady = loading;
  await loading;
}

async function showOriginalSelection(page, bbox) {
  const mounted = readerState;
  if (!mounted) return;
  const anchor=originalSelection(mounted.pdfArtifact?.report,page,bbox);
  await switchPdfLanguage("original");
  if (readerState !== mounted) return;
  if (anchor) resolveAnchor({...anchor,sourceVersion:mounted.doc.version.id});
  else toast("対応する原文ブロックを確定できませんでした。原文の対応位置を表示しています。");
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
    el("button", { class: "btn btn--sm", type: "button", onClick: () => openImportBridge() }, "ChatGPTから取り込む"), mdBtn,
  );
}

function setViewMode(mode) {
  if (!readerState) return;
  pdfviewer.rememberPosition();
  readerState.viewMode = mode;
  readerState.els.blockContainer.hidden = mode !== "block";
  readerState.els.pdfContainer.hidden = mode !== "pdf";
  if (readerState.els.toggleButtons) {
    readerState.els.toggleButtons.pdf.classList.toggle("active", mode === "pdf");
    readerState.els.toggleButtons.block.classList.toggle("active", mode === "block");
  }
  if (mode === "block") readerState.els.blockContainer.scrollTop = loadReaderUi(readerState.sourceId).blockScrollTop || 0;
  if (readerState.els.pdfControls) readerState.els.pdfControls.hidden = mode !== "pdf";
  saveReaderUi(readerState.sourceId, { pdfMode: mode });
  pdfviewer.refresh();
}

// ---------- tabs ----------

function switchTab(tabKey, opts = {}) {
  if (!readerState) return;
  readerState.activeTab = tabKey;
  saveReaderUi(readerState.sourceId, { activeTab: tabKey });

  for (const btn of readerState.els.tabsHeader.querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.tab === tabKey);
  }
  readerState.els.briefPanel.hidden = tabKey !== "brief";
  readerState.els.summaryPanel.hidden = tabKey !== "summary";
  readerState.els.qaPanel.hidden = tabKey !== "qa";
  readerState.els.notePanel.hidden = tabKey !== "note";

  paperbrief.unmount();
  summary.unmount();
  qa.unmount();
  note.unmount();

  if (tabKey === "brief") {
    paperbrief.mount(readerState.els.briefPanel, readerState.sourceId, {
      onEvidence: navigateBriefEvidence,
      isPdf: readerState.source.type === "pdf",
      onPdf: () => readerState.source.type === "pdf" ? switchPdfLanguage("original") : setViewMode("block"),
      onImport: () => openImportBridge(),
    });
  } else if (tabKey === "summary") {
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
  pdfviewer.rememberPosition();
  activeDrag.pane.style.flex = `0 0 ${pct}%`;
  activeDrag.pct = pct;
});
document.addEventListener("mouseup", () => {
  if (!activeDrag) return;
  activeDrag.divider.classList.remove("dragging");
  saveReaderUi(activeDrag.sourceId, { paneWidthPct: activeDrag.pct });
  if (readerState?.sourceId === activeDrag.sourceId) readerState.ui.paneWidthPct = activeDrag.pct;
  pdfviewer.refresh();
  activeDrag = null;
});

function applyPaneWidth() {
  const pct = readerState.ui.paneWidthPct || 55;
  const { leftPane, rightPane, divider, panelToggle, split } = readerState.els;
  const collapsed = !!readerState.ui.sidePanelCollapsed;
  leftPane.style.flex = collapsed ? "1 1 100%" : `0 0 ${pct}%`;
  rightPane.hidden = divider.hidden = collapsed;
  split.classList.toggle("reader-split--full", collapsed);
  panelToggle.textContent = collapsed ? "サイドパネルを表示" : "PDFを全幅で読む";
  panelToggle.setAttribute("aria-expanded", String(!collapsed));
}
function toggleSidePanel() {
  if (!readerState) return;
  pdfviewer.rememberPosition();
  readerState.ui.sidePanelCollapsed = !readerState.ui.sidePanelCollapsed;
  saveReaderUi(readerState.sourceId, { sidePanelCollapsed: readerState.ui.sidePanelCollapsed });
  applyPaneWidth();
  pdfviewer.refresh();
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

function persistReaderPosition() {
  if (!readerState?.els) return;
  pdfviewer.rememberPosition();
  if (!readerState.els.blockContainer.hidden) saveReaderUi(readerState.sourceId, { blockScrollTop: readerState.els.blockContainer.scrollTop });
}
function restoreScroll() {
  const mounted = readerState;
  const { blockContainer } = mounted.els;
  blockContainer.scrollTop = mounted.ui.blockScrollTop ?? (mounted.viewMode === "block" ? mounted.ui.scrollTop : 0);
  blockContainer.addEventListener("scroll", debounce(() => {
    if (readerState !== mounted || blockContainer.hidden) return;
    saveReaderUi(mounted.sourceId, { blockScrollTop: blockContainer.scrollTop });
  }, 200));
}

// ---------- in-document search ----------

async function runSearch(query) {
  const mounted=readerState, revision=++searchRevision;
  const japanese=mounted?.viewMode === "pdf" && mounted.pdfLanguage === "japanese";
  let matches;
  try { matches=japanese ? await pdfviewer.findTextMatches(query) : docviewer.findTextMatches(query); }
  catch { if (readerState === mounted) toast("PDF内検索に失敗しました。再試行してください。"); return; }
  if (readerState !== mounted || revision !== searchRevision) return;
  searchState = { query, matches, idx: -1, japanese };
  if (mounted.viewMode === "block") docviewer.markSearchMatches(matches, -1);
  updateSearchCount();
}

async function searchNext() {
  const mounted=readerState;
  if (!mounted) return;
  const q = readerState.els.searchInput.value.trim();
  const japanese=readerState.viewMode === "pdf" && readerState.pdfLanguage === "japanese";
  if (q !== searchState.query || japanese !== searchState.japanese) await runSearch(q);
  if (readerState !== mounted) return;
  if (!searchState.matches.length) { updateSearchCount(); return; }
  searchState.idx = (searchState.idx + 1) % searchState.matches.length;
  if (searchState.japanese) {
    const match=searchState.matches[searchState.idx];
    pdfviewer.scrollToPage(match.page,match.quote);
  } else {
    const blockIdx = searchState.matches[searchState.idx];
    const block = readerState.doc.blocks.find((b) => b.idx === blockIdx);
    if (readerState.viewMode === "block") {
      docviewer.markSearchMatches(searchState.matches, blockIdx);
      docviewer.jumpToBlockIdx(blockIdx);
    } else if (block?.page) pdfviewer.scrollToPage(block.page,block.text.slice(0,80));
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
  if (readerState.pdfLanguage === "japanese") {
    const mounted = readerState;
    switchPdfLanguage("original").then(() => { if (readerState === mounted) resolveAnchor(rawAnchor); });
    return;
  }
  const anchor = parseAnchor(rawAnchor);
  const result = resolveSourceAnchor(anchor, readerState.doc.blocks || [], readerState.doc.version);
  readerState.els.leftPane.querySelector(".anchor-candidates")?.remove();
  const jump = (block) => {
    if (readerState.source.type === "pdf" && readerState.viewMode === "pdf" && block.page) {
      if (!pdfviewer.scrollToEvidence(block.page, block.bbox)) toast("PDFの読み込み完了後に再試行してください。");
    } else {
      setViewMode("block");
      docviewer.scrollToBlockId(block.id);
    }
  };
  if (result.status === "resolved") { jump(result.block); return result; }
  if (result.status === "candidates") {
    const panel = el("div", { class: "anchor-candidates", role: "status" },
      el("p", {}, "根拠位置を確定できません。候補を確認してください。"));
    for (const block of result.candidates) {
      panel.append(el("button", { class: "btn btn--sm", type: "button", onClick: () => jump(block) },
        `p.${block.page || "?"} · ${block.text.slice(0, 100) || block.role || block.kind}`));
    }
    readerState.els.leftPane.prepend(panel);
  } else if (result.status === "page_only" && readerState.source.type === "pdf") {
    setViewMode("pdf");
    pdfviewer.scrollToPage(result.page); // no quote highlight: page is only a coarse hint
    toast("ページのみ表示しました。根拠位置は未解決です。");
  } else toast("原文位置を特定できませんでした(資料が更新された可能性)");
  return result;
}

// Always switch to the original PDF and reuse Phase 1 resolution against current blocks.
async function navigateBriefEvidence(anchor, kind = "resolved") {
  const mounted = readerState;
  if (!mounted) return;
  if (mounted.source.type === "pdf") {
    await switchPdfLanguage("original");
    await mounted.pdfReady;
    if (readerState !== mounted) return;
  }
  mounted.els.leftPane.querySelector(".brief-navigation-notice")?.remove();
  pdfviewer.clearEvidence();
  if (kind === "candidate" || kind === "page_only") {
    mounted.els.leftPane.prepend(el("p", { class: "brief-navigation-notice", role: "status" },
      kind === "candidate" ? "候補の閲覧中 · 根拠未確定。保存済みEvidenceは変更されません。" : "ページのみ表示 · 根拠未確定。"));
  }
  resolveAnchor(anchor);
  if (kind === "candidate") mounted.els.pdfContainer.querySelectorAll(".pdf-evidence-marker").forEach((marker) => {
    marker.style.borderStyle = "dashed";
  });
}
