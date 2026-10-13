// PDF.js controller: CSS-point geometry is independent of bounded raster pixels.
// Lightweight page shells stay mounted for reliable scrolling/evidence navigation.
import { geometry } from "../anchor.js";
import * as pdfjsLib from "/vendor/pdf.mjs";
import { el, flashElement, normalizeWhitespace } from "../util.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdf.worker.mjs";
const MAX_PIXELS = 6_000_000; // <= 24 MB RGBA per page
const MAX_DIMENSION = 8192;
const MAX_RENDERED_PAGES = 5; // <= 120 MB resident page rasters
const CSS_UNITS = 96 / 72;
let session = null;

export async function mount(container, { fileUrl, view = {}, onChange = () => {} }) {
  unmount();
  const s = session = { container, records: new Map(), onChange, disposed: false,
    mode: ["width", "page", "custom"].includes(view.mode) ? view.mode : "width",
    zoom: clampZoom(view.zoom), position: view.position, legacyScrollTop: view.scrollTop || 0,
    running: false, wanted: [], revision: 0, dpr: window.devicePixelRatio || 1 };
  container.textContent = "";
  container.classList.add("pdf-scroll");
  const loading = el("div", { class: "pdf-loading", role: "status" }, "PDFを読み込み中...");
  container.append(loading);
  s.onScroll = () => {
    if (!s.prepared || s.reflowing || !container.clientHeight) return;
    s.position = capturePosition(s);
    schedule(s);
    clearTimeout(s.saveTimer);
    s.saveTimer = setTimeout(() => publish(s), 150);
  };
  s.onPageHide = () => {
    if (s.prepared && container.clientHeight) s.position = capturePosition(s);
    publish(s);
  };
  container.addEventListener("scroll", s.onScroll);
  window.addEventListener("pagehide", s.onPageHide);
  s.resize = new ResizeObserver(() => {
    clearTimeout(s.resizeTimer);
    s.resizeTimer = setTimeout(() => reflow(s), 100);
  });
  s.resize.observe(container);
  s.onWindowResize = () => {
    clearTimeout(s.resizeTimer);
    s.resizeTimer = setTimeout(() => reflow(s), 100);
  };
  window.addEventListener("resize", s.onWindowResize); // also detects monitor/DPR changes
  try {
    s.loading = pdfjsLib.getDocument({ url: fileUrl });
    s.doc = await s.loading.promise;
    if (s.disposed) return;
    // Read geometry only, without allocating canvases or text layers for all pages.
    for (let num = 1; num <= s.doc.numPages; num++) {
      const page = await s.doc.getPage(num);
      if (s.disposed) return;
      const base = page.getViewport({ scale: CSS_UNITS });
      const pageWrap = el("div", { class: "pdf-page", dataset: { pdfPage: String(num) } },
        el("div", { class: "pdf-page__placeholder", role: "status" }, "描画待ち"),
        el("div", { class: "pdf-page__number" }, `${num} / ${s.doc.numPages}`));
      s.records.set(num, { num, base, pageWrap, view: [...page.view], rotation: page.rotate,
        generation: 0, rendered: false });
      container.append(pageWrap);
      page.cleanup();
    }
    loading.remove();
    s.prepared = true;
    reflow(s, true);
  } catch (err) {
    if (s.disposed) return;
    loading.textContent = `PDFの表示に失敗しました: ${err.message || err}`;
  }
}

function clampZoom(value) {
  const num = Number(value);
  return Number.isFinite(num) && num > 0 ? Math.min(2.5, Math.max(.75, num)) : 1;
}

export function setZoom(value) {
  if (!session) return;
  session.mode = "custom";
  session.zoom = clampZoom(value);
  reflow(session, true);
  return session.zoom;
}
export function fit(mode) {
  if (!session || !["width", "page"].includes(mode)) return;
  session.mode = mode;
  if (mode === "page" && session.position) session.position = { ...session.position, x: .5, y: .5 };
  reflow(session, true);
}
// Called before hiding the PDF or changing pane width; hidden scroll events are ignored.
export function rememberPosition() {
  if (session?.prepared && session.container.clientHeight && session.records.size) {
    session.position = capturePosition(session);
    publish(session);
  }
}
export function refresh() { if (session) reflow(session, true); }
export function pageCount() { return session?.records.size || 0; }

function capturePosition(s) {
  const box = s.container.getBoundingClientRect();
  const cx = box.left + s.container.clientWidth / 2;
  const cy = box.top + s.container.clientHeight / 2;
  let nearest, distance = Infinity;
  for (const r of s.records.values()) {
    const b = r.pageWrap.getBoundingClientRect();
    const d = cy < b.top ? b.top - cy : cy > b.bottom ? cy - b.bottom : 0;
    if (d < distance) { nearest = { page: r.num,
      x: Math.min(1, Math.max(0, (cx - b.left) / b.width)),
      y: Math.min(1, Math.max(0, (cy - b.top) / b.height)) }; distance = d; }
    if (d === 0) break;
  }
  return nearest;
}
function restorePosition(s) {
  const p = s.position, r = s.records.get(Number(p?.page));
  if (!r) { s.container.scrollTop = s.legacyScrollTop; return; }
  const b = r.pageWrap.getBoundingClientRect(), box = s.container.getBoundingClientRect();
  const ratio = v => Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0;
  s.container.scrollTop += b.top - box.top + b.height * ratio(p.y) - s.container.clientHeight / 2;
  s.container.scrollLeft += b.left - box.left + b.width * ratio(p.x) - s.container.clientWidth / 2;
}
function publish(s) {
  if (s.disposed || !s.prepared) return;
  const reference = s.records.get(s.position?.page) || s.records.get(1);
  s.onChange({ mode: s.mode, zoom: s.zoom, position: s.position,
    percent: reference?.viewport ? Math.round(reference.viewport.scale / CSS_UNITS * 100) : 100,
    pages: s.records.size });
}
function reflow(s, force = false) {
  if (s.disposed || !s.prepared) return;
  const width = s.container.clientWidth, height = s.container.clientHeight;
  if (!width || !height) { schedule(s); return; }
  const dpr = window.devicePixelRatio || 1;
  if (!force && s.width === width && s.height === height && s.dpr === dpr) return;
  s.width = width; s.height = height; s.dpr = dpr;
  s.revision++;
  s.reflowing = true;
  for (const r of s.records.values()) {
    release(r);
    const zoom = s.mode === "custom" ? s.zoom : Math.max(.1, Math.min(2.5,
      (width - 48) / r.base.width,
      s.mode === "page" ? (height - 48) / r.base.height : Infinity));
    r.viewport = r.base.clone({ scale: CSS_UNITS * zoom });
    r.pageWrap.style.width = `${r.viewport.width}px`;
    r.pageWrap.style.height = `${r.viewport.height}px`;
    r.pageWrap.style.setProperty("--scale-factor", r.viewport.scale);
    r.error = false;
    r.pageWrap.querySelector(".pdf-page__placeholder").textContent = "描画待ち";
    positionMarker(r);
  }
  restorePosition(s);
  s.position = capturePosition(s);
  s.reflowing = false;
  publish(s);
  schedule(s);
}

function release(r) {
  r.generation++;
  r.renderTask?.cancel();
  r.textTask?.cancel();
  r.canvas?.remove();
  r.textLayerDiv?.remove();
  // Pending canvas is released in renderPage's finally, after cancellation settles.
  if (!r.pending && r.canvas) r.canvas.width = r.canvas.height = 0;
  r.canvas = r.textLayerDiv = null;
  r.rendered = false;
  r.pageWrap.classList.remove("pdf-page--ready");
}
function schedule(s) {
  if (s.disposed) return;
  if (!s.container.clientHeight) {
    s.wanted = [];
  } else {
    const box = s.container.getBoundingClientRect();
    const center = box.top + s.container.clientHeight / 2;
    const ordered = [...s.records.values()].map(r => {
      const b = r.pageWrap.getBoundingClientRect();
      return { r, visible: b.bottom > box.top && b.top < box.top + s.container.clientHeight,
        distance: Math.abs((b.top + b.bottom) / 2 - center) };
    }).sort((a, b) => Number(b.visible) - Number(a.visible) || a.distance - b.distance);
    // Visible first, then one page beyond either edge; hard cap includes in-flight work.
    const visible = ordered.filter(v => v.visible).map(v => v.r.num);
    s.wanted = ordered.filter(v => v.visible || visible.some(n => Math.abs(n - v.r.num) === 1))
      .slice(0, MAX_RENDERED_PAGES).map(v => v.r);
  }
  for (const r of s.records.values()) {
    const selected = window.getSelection();
    const selectionInside = selected && !selected.isCollapsed && r.textLayerDiv?.contains(selected.anchorNode);
    if (!s.wanted.includes(r) && (r.rendered || r.pending)) {
      // Do not remove DOM under a live selection. It counts against the same cap.
      if (selectionInside && s.container.clientHeight) {
        const last = s.wanted.pop();
        if (last && last !== r) release(last);
        s.wanted.push(r);
      } else release(r);
    }
  }
  if (!s.running) void pump(s);
}
async function pump(s) {
  s.running = true;
  try {
    while (!s.disposed) {
      const r = s.wanted.find(r => !r.rendered && !r.error && !r.pending);
      if (!r) break;
      await renderPage(s, r);
    }
  } finally { s.running = false; }
}
async function renderPage(s, r) {
  const generation = r.generation, revision = s.revision;
  const valid = () => !s.disposed && r.generation === generation && s.revision === revision;
  r.pending = true;
  let canvas, page;
  try {
    page = await s.doc.getPage(r.num);
    if (!valid()) return;
    const viewport = r.viewport;
    const outputScale = Math.min(Math.max(1, s.dpr),
      Math.sqrt(MAX_PIXELS / (viewport.width * viewport.height)),
      MAX_DIMENSION / viewport.width, MAX_DIMENSION / viewport.height);
    canvas = el("canvas", { width: String(Math.max(1, Math.floor(viewport.width * outputScale))),
      height: String(Math.max(1, Math.floor(viewport.height * outputScale))),
      style: { width: `${viewport.width}px`, height: `${viewport.height}px` } });
    const textLayerDiv = el("div", { class: "textLayer", dataset: { pdfPage: String(r.num) } });
    r.canvas = canvas; r.textLayerDiv = textLayerDiv;
    r.pageWrap.append(canvas, textLayerDiv);
    r.renderTask = page.render({ canvasContext: canvas.getContext("2d", { alpha: false }), viewport,
      transform: [canvas.width / viewport.width, 0, 0, canvas.height / viewport.height, 0, 0] });
    await r.renderTask.promise;
    if (!valid()) return;
    const textContent = await page.getTextContent();
    if (!valid()) return;
    r.textTask = new pdfjsLib.TextLayer({ textContentSource: textContent, container: textLayerDiv, viewport });
    await r.textTask.render();
    if (!valid()) return;
    r.rendered = true;
    r.pageWrap.classList.add("pdf-page--ready");
    if (r.quote) { highlightQuoteInPage(r, r.quote); r.quote = null; }
  } catch (err) {
    if (valid() && !["RenderingCancelledException", "AbortException"].includes(err.name)) {
      r.error = true;
      r.pageWrap.querySelector(".pdf-page__placeholder").textContent = `ページの描画に失敗しました: ${err.message}`;
    }
  } finally {
    r.pending = false;
    r.renderTask = r.textTask = null;
    if (!valid() || r.error) {
      canvas?.remove();
      if (canvas) canvas.width = canvas.height = 0;
      r.textLayerDiv?.remove();
    }
    page?.cleanup();
  }
}

export function unmount() {
  const s = session;
  if (!s) return;
  rememberPosition();
  s.disposed = true;
  clearTimeout(s.saveTimer); clearTimeout(s.resizeTimer);
  s.resize.disconnect();
  s.container.removeEventListener("scroll", s.onScroll);
  window.removeEventListener("resize", s.onWindowResize);
  window.removeEventListener("pagehide", s.onPageHide);
  for (const r of s.records.values()) { clearTimeout(r.markerTimer); release(r); }
  // Destroy the transport/worker even if getDocument or a page render is pending.
  s.loading?.destroy().catch(() => {});
  s.container.textContent = "";
  session = null;
}

export function clearEvidence() {
  if (!session) return;
  for (const r of session.records.values()) {
    r.evidence = null;
    clearTimeout(r.markerTimer);
    r.pageWrap.querySelector(".pdf-evidence-marker")?.remove();
  }
}

/** All page shells have geometry, including pages that have never rendered. */
export function scrollToPage(pageNum, quote) {
  const r = session?.records.get(Number(pageNum));
  if (!r?.viewport) return false;
  r.pageWrap.scrollIntoView({ behavior: "instant", block: "start" });
  flashElement(r.pageWrap);
  for (const record of session.records.values()) record.quote = null;
  r.quote = quote;
  if (quote && r.rendered) { highlightQuoteInPage(r, quote); r.quote = null; }
  session.position = capturePosition(session);
  schedule(session); publish(session);
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


/** Convert a single-page DOM selection back to unrotated source points. */
export function selectionGeometry(pageNum, range) {
  const record = session?.records.get(Number(pageNum));
  if (!record?.rendered || !record.textLayerDiv.contains(range.endContainer)) return null;
  const bounds = record.pageWrap.getBoundingClientRect();
  const rectangles = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
  if (!rectangles.length) return null;
  const points = rectangles.flatMap((r) => [[r.left, r.top], [r.right, r.bottom]])
    .map(([x, y]) => record.viewport.convertToPdfPoint(
      (x - bounds.left) * record.viewport.width / bounds.width,
      (y - bounds.top) * record.viewport.height / bounds.height))
    .map(([x, y]) => [x - record.view[0], record.view[3] - y]);
  const width = record.view[2] - record.view[0], height = record.view[3] - record.view[1];
  return geometry({ coordinate_system: "pymupdf_unrotated", units: "pt",
    rect: [Math.max(0, Math.min(...points.map((p) => p[0]))),
      Math.max(0, Math.min(...points.map((p) => p[1]))),
      Math.min(width, Math.max(...points.map((p) => p[0]))),
      Math.min(height, Math.max(...points.map((p) => p[1])))],
    page_rect: [0, 0, width, height], rotation: record.viewport.rotation });
}

/** Scroll to verified evidence geometry, with a temporary region marker. */
export function scrollToEvidence(pageNum, bbox) {
  const record = session?.records.get(Number(pageNum)), g = geometry(bbox);
  if (!record?.viewport) return false;
  if (!g) return scrollToPage(pageNum); // legacy geometry is unknown
  clearEvidence();
  for (const r of session.records.values()) r.quote = null;
  record.evidence = g;
  positionMarker(record);
  const marker = record.pageWrap.querySelector(".pdf-evidence-marker");
  marker.scrollIntoView({ behavior: "instant", block: "center" });
  session.position = capturePosition(session);
  schedule(session); publish(session);
  clearTimeout(record.markerTimer);
  record.markerTimer = setTimeout(() => {
    record.evidence = null;
    record.pageWrap.querySelector(".pdf-evidence-marker")?.remove();
  }, 2400);
  return true;
}

function positionMarker(record) {
  const previous = record.pageWrap.querySelector(".pdf-evidence-marker");
  const borderStyle = previous?.style.borderStyle || "solid";
  previous?.remove();
  if (!record.evidence) return;
  const [x0, y0, x1, y1] = record.evidence.rect;
  const r = record.viewport.convertToViewportRectangle([
    record.view[0] + x0, record.view[3] - y1,
    record.view[0] + x1, record.view[3] - y0]);
  record.pageWrap.append(el("div", { class: "pdf-evidence-marker", style: {
    left: `${Math.min(r[0], r[2])}px`, top: `${Math.min(r[1], r[3])}px`,
    width: `${Math.abs(r[2] - r[0])}px`, height: `${Math.abs(r[3] - r[1])}px`, borderStyle,
  } }));
}
