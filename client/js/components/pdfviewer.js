// client/js/components/pdfviewer.js
// pdf.js rendering: sequential canvas + selectable TextLayer per page, page toggle support.
// Bounding-box highlight rendering on the canvas itself is out of MVP scope
// (data_model.md roadmap note); highlights created while in PDF mode still persist
// and become visible as <mark> once the block/"テキスト表示" view is opened.

import * as pdfjsLib from "/vendor/pdf.mjs";
import { el, flashElement, normalizeWhitespace } from "../util.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.mjs";

let pageRecords = new Map(); // pageNum -> { pageWrap, canvas, textLayerDiv }
let currentContainer = null;
let loadToken = 0; // guards against stale async renders when mount() is called again

export async function mount(container, { fileUrl }) {
  const token = ++loadToken;
  currentContainer = container;
  pageRecords = new Map();
  container.textContent = "";
  container.classList.add("pdf-scroll");

  const loadingEl = el("div", { class: "pdf-loading" },
    el("div", { class: "spinner spinner--lg" }),
    el("div", {}, "PDFを読み込み中..."),
  );
  container.append(loadingEl);

  try {
    const loadingTask = pdfjsLib.getDocument({ url: fileUrl });
    const pdfDoc = await loadingTask.promise;
    if (token !== loadToken) return; // superseded by a newer mount() call

    loadingEl.remove();
    const targetWidth = Math.max((container.clientWidth || 800) - 48, 320);

    for (let pageNum = 1; pageNum <= pdfDoc.numPages; pageNum++) {
      if (token !== loadToken) return;
      const page = await pdfDoc.getPage(pageNum);
      const baseViewport = page.getViewport({ scale: 1 });
      const scale = targetWidth / baseViewport.width;
      const viewport = page.getViewport({ scale });

      const canvas = el("canvas", {
        width: String(Math.max(1, Math.floor(viewport.width))),
        height: String(Math.max(1, Math.floor(viewport.height))),
      });
      const textLayerDiv = el("div", {
        class: "textLayer",
        dataset: { pdfPage: String(pageNum) },
        style: { width: `${viewport.width}px`, height: `${viewport.height}px` },
      });
      const pageWrap = el("div", {
        class: "pdf-page",
        dataset: { pdfPage: String(pageNum) },
        style: { width: `${viewport.width}px`, height: `${viewport.height}px` },
      }, canvas, textLayerDiv, el("div", { class: "pdf-page__number" }, `${pageNum} / ${pdfDoc.numPages}`));
      container.append(pageWrap);

      const ctx = canvas.getContext("2d");
      await page.render({ canvasContext: ctx, viewport }).promise;
      if (token !== loadToken) return;

      const textContent = await page.getTextContent();
      const textLayer = new pdfjsLib.TextLayer({ textContentSource: textContent, container: textLayerDiv, viewport });
      await textLayer.render();

      pageRecords.set(pageNum, { pageWrap, canvas, textLayerDiv, viewport });
    }
  } catch (err) {
    if (token !== loadToken) return;
    loadingEl.remove();
    container.append(el("div", { class: "pdf-loading" }, `PDFの表示に失敗しました: ${err && err.message ? err.message : err}`));
  }
}

export function unmount() {
  loadToken++; // invalidate any in-flight page renders
  pageRecords = new Map();
  if (currentContainer) currentContainer.textContent = "";
}

export function pageCount() {
  return pageRecords.size;
}

/** Scroll to a page (1-based) and best-effort highlight a quote within its text layer. */
export function scrollToPage(pageNum, quote) {
  const record = pageRecords.get(pageNum);
  if (!record) return false;
  record.pageWrap.scrollIntoView({ behavior: "smooth", block: "start" });
  flashElement(record.pageWrap);
  if (quote) highlightQuoteInPage(record, quote);
  return true;
}

function highlightQuoteInPage(record, quote) {
  const spans = Array.from(record.textLayerDiv.querySelectorAll("span"));
  if (!spans.length) return;
  let full = "";
  const map = [];
  for (const span of spans) {
    const t = span.textContent || "";
    map.push({ span, start: full.length, end: full.length + t.length });
    full += t;
  }
  const rawNeedle = quote.slice(0, 80);
  let start = full.indexOf(rawNeedle);
  if (start === -1) {
    const normFull = normalizeWhitespace(full);
    const normNeedle = normalizeWhitespace(quote).slice(0, 80);
    start = normFull.indexOf(normNeedle);
  }
  if (start === -1) return;
  const end = start + rawNeedle.length;
  const hitSpans = map.filter((m) => m.end > start && m.start < end).map((m) => m.span);
  hitSpans.forEach((s) => {
    s.classList.add("search-hit-active");
    setTimeout(() => s.classList.remove("search-hit-active"), 2100);
  });
  if (hitSpans[0]) hitSpans[0].scrollIntoView({ behavior: "smooth", block: "center" });
}
