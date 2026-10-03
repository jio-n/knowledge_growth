// client/js/components/selection.js
// Selection popover shown on mouseup inside the reader's left (source) pane, and
// SourceAnchor construction (source_anchor_spec.md "作成(クライアント側)").

import { resolveSourceAnchor } from "../anchor.js";
import * as pdfviewer from "./pdfviewer.js";
import { el, toast } from "../util.js";
import { api } from "../api.js";
import { openSaveDialog } from "./savedialog.js";
import * as docviewer from "./docviewer.js";
import * as qa from "./qa.js";
import { QUICK_PROMPT_BUTTONS } from "../state.js";

let ctx = null; // { root, sourceId, getBlocks, switchTab }
let popoverEl = null;
let extraCardEl = null;

/** init(root, context) - call once per reader mount; root = the left-pane element
 *  that contains BOTH the block view and the pdf view (toggled via [hidden]). */
export function init(root, context) {
  unmount();
  ctx = { root, ...context };
  root.addEventListener("mouseup", onMouseUp);
}

export function unmount() {
  ctx?.root.removeEventListener("mouseup", onMouseUp);
  ctx = null;
  closeAll();
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAll();
});
document.addEventListener("mousedown", (e) => {
  if (popoverEl && !popoverEl.contains(e.target)) closePopover();
  if (extraCardEl && !extraCardEl.contains(e.target)) closeExtraCard();
});

function onMouseUp(e) {
  if (popoverEl && popoverEl.contains(e.target)) return;
  if (!ctx) return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) { closePopover(); return; }
  const text = sel.toString().trim();
  if (!text) { closePopover(); return; }
  const range = sel.getRangeAt(0);
  if (!ctx.root.contains(range.commonAncestorContainer)) { closePopover(); return; }

  const info = buildAnchorInfo(range);
  if (!info) { closePopover(); return; }
  showPopover(range, info);
}

function closestFromNode(node, selector) {
  const elNode = node.nodeType === 1 ? node : node.parentElement;
  return elNode ? elNode.closest(selector) : null;
}

function computeQuoteContext(range, container) {
  const full = container.textContent || "";
  const quote = range.toString();
  if (!quote) return null;
  const preRange = document.createRange();
  preRange.selectNodeContents(container);
  try {
    preRange.setEnd(range.startContainer, range.startOffset);
  } catch {
    return null;
  }
  const startOffset = preRange.toString().length;
  const endOffset = startOffset + Math.min(quote.length, 500);
  const prefix = full.slice(Math.max(0, startOffset - 60), startOffset);
  const suffix = full.slice(endOffset, endOffset + 60);
  return {
    quote: quote.slice(0, 500),
    prefix: prefix.slice(-60),
    suffix: suffix.slice(0, 60),
    fullLength: full.length,
  };
}

function buildAnchorInfo(range) {
  const startNode = range.startContainer;
  const blockContainer = closestFromNode(startNode, "[data-block-id]");
  const pdfContainer = closestFromNode(startNode, ".textLayer[data-pdf-page]");

  if (blockContainer) {
    // A multi-block selection has no single authoritative block identity.
    if (!blockContainer.contains(range.endContainer)) return null;
    const qc = computeQuoteContext(range, blockContainer);
    if (!qc || !qc.quote.trim()) return null;
    const anchor = {
      type: "text-quote",
      quote: qc.quote,
      prefix: qc.prefix,
      suffix: qc.suffix,
      blockId: blockContainer.dataset.blockId || null,
      blockIdx: blockContainer.dataset.blockIdx !== undefined && blockContainer.dataset.blockIdx !== ""
        ? Number(blockContainer.dataset.blockIdx) : null,
      page: blockContainer.dataset.page ? Number(blockContainer.dataset.page) : null,
      headingPath: blockContainer.dataset.headingPath || null,
      sourceVersion: ctx.getVersion?.()?.id || null,
      sourceHash: ctx.getVersion?.()?.content_hash || null,
      bbox: ctx.getBlocks?.().find((b) => b.id === blockContainer.dataset.blockId)?.bbox || null,
    };
    const coverageRatio = qc.fullLength > 0 ? qc.quote.length / qc.fullLength : 0;
    return { anchor, mode: "block", coverageRatio };
  }

  if (pdfContainer) {
    const qc = computeQuoteContext(range, pdfContainer);
    if (!qc || !qc.quote.trim()) return null;
    const page = Number(pdfContainer.dataset.pdfPage);
    const blocks = ctx.getBlocks ? ctx.getBlocks() : [];
    const bbox = pdfviewer.selectionGeometry(page, range);
    if (!bbox) return null;
    const version = ctx.getVersion?.() || {};
    const portable = { page, bbox, quote: qc.quote, sourceVersion: version.id, sourceHash: version.content_hash };
    const resolution = resolveSourceAnchor(portable, blocks, version);
    const approxBlock = resolution.status === "resolved" ? resolution.block : null;
    const anchor = {
      type: "text-quote",
      quote: qc.quote,
      prefix: null, // PDF drawing order is not reliable textual context
      suffix: null,
      sourceVersion: version.id || null,
      sourceHash: version.content_hash || null,
      bbox,
      blockId: approxBlock?.id || null,
      blockIdx: approxBlock ? approxBlock.idx : null,
      page,
      headingPath: approxBlock ? (approxBlock.heading_path || null) : null,
    };
    return { anchor, mode: "pdf", coverageRatio: 0 };
  }

  return null;
}

function showPopover(range, info) {
  closePopover();
  closeExtraCard();
  const rect = range.getBoundingClientRect();

  const buttons = [
    mkBtn("質問する", () => doAskFree(info)),
    ...QUICK_PROMPT_BUTTONS.map((qp) => mkBtn(qp.label, () => doQuickPrompt(qp.promptType, info))),
    mkBtn("翻訳", () => doTranslate(range, info)),
    mkBtn("ハイライト", () => doHighlight(info)),
    mkBtn("原文を保存", () => doSaveQuote(info)),
  ];

  popoverEl = el("div", {
    class: "selection-popover",
    style: { left: `${Math.max(8, rect.left)}px`, top: `${rect.bottom + 8}px` },
  }, ...buttons);
  document.body.append(popoverEl);

  requestAnimationFrame(() => {
    if (!popoverEl) return;
    const pr = popoverEl.getBoundingClientRect();
    if (pr.right > window.innerWidth - 8) {
      popoverEl.style.left = `${Math.max(8, window.innerWidth - pr.width - 8)}px`;
    }
    if (pr.bottom > window.innerHeight - 8) {
      popoverEl.style.top = `${Math.max(8, rect.top - pr.height - 8)}px`;
    }
  });
}

function mkBtn(label, handler) {
  return el("button", { type: "button", onClick: () => handler() }, label);
}

function closePopover() {
  if (popoverEl) { popoverEl.remove(); popoverEl = null; }
}
function closeExtraCard() {
  if (extraCardEl) { extraCardEl.remove(); extraCardEl = null; }
}
function closeAll() {
  closePopover();
  closeExtraCard();
}

// ---------- actions ----------

function doAskFree(info) {
  closePopover();
  ctx.switchTab("qa");
  qa.prefillFreeQuestion({ anchor: info.anchor, selectionText: info.anchor.quote });
}

function doQuickPrompt(promptType, info) {
  closePopover();
  ctx.switchTab("qa");
  qa.submitQuickQuestion({
    anchor: info.anchor,
    selectionText: info.anchor.quote,
    promptType,
  });
}

async function doTranslate(range, info) {
  const rect = range.getBoundingClientRect();
  closePopover();
  try {
    if (info.mode === "block" && info.coverageRatio >= 0.8 && info.anchor.blockId) {
      const res = await api.translate({ source_id: ctx.sourceId, text: info.anchor.quote, block_id: info.anchor.blockId });
      docviewer.setTranslation(info.anchor.blockId, res.translation, true);
      toast("段落の下に翻訳を表示しました", { type: "success" });
    } else {
      const res = await api.translate({ source_id: ctx.sourceId, text: info.anchor.quote, block_id: null });
      showEphemeralTranslation(rect, info, res.translation);
    }
  } catch {
    // api.js already toasted
  }
}

function showEphemeralTranslation(rect, info, translationText) {
  closeExtraCard();
  extraCardEl = el("div", {
    class: "ephemeral-translation-card",
    style: { left: `${Math.max(8, rect.left)}px`, top: `${rect.bottom + 8}px` },
  },
    el("div", { class: "ephemeral-translation-card__text" }, translationText),
    el("button", {
      class: "btn btn--sm",
      type: "button",
      onClick: () => {
        closeExtraCard();
        openSaveDialog({
          sourceId: ctx.sourceId,
          content: `${info.anchor.quote} → ${translationText}`,
          anchor: info.anchor,
          origin: "llm",
          infoType: "term",
          sectionKey: "terms",
          promptType: "translate",
          lockDefaults: true, // "用語として保存" is an explicit term-save action
          onViewRequested: (item) => ctx.switchTab("note", { flashId: item.id }),
        });
      },
    }, "用語として保存"),
  );
  document.body.append(extraCardEl);
}

async function doHighlight(info) {
  closePopover();
  try {
    const res = await api.createHighlight({ source_id: ctx.sourceId, anchor: info.anchor, color: "yellow", comment: null });
    docviewer.applyHighlightMark({ id: res.id, anchor: info.anchor, color: "yellow" });
    toast("ハイライトしました", { type: "success" });
  } catch {
    // api.js already toasted
  }
}

function doSaveQuote(info) {
  closePopover();
  openSaveDialog({
    sourceId: ctx.sourceId,
    content: info.anchor.quote,
    anchor: info.anchor,
    origin: "source_quote",
    infoType: "fact",
    sectionKey: null,
    promptType: null,
    onViewRequested: (item) => ctx.switchTab("note", { flashId: item.id }),
  });
}
