// client/js/util.js
// Small shared DOM / formatting helpers used across the app.

/**
 * el(tag, attrs, ...children) - lightweight DOM builder.
 * attrs: object of attributes/props. Special keys:
 *   class      -> className
 *   dataset    -> object merged into el.dataset
 *   on<Event>  -> addEventListener (e.g. onClick, onInput)
 *   html       -> sets innerHTML (ONLY use for trusted/marked output, never raw source text)
 *   style      -> object merged into el.style, or a string
 * children: strings, numbers, Nodes, or (nested) arrays of those. null/false/undefined skipped.
 */
export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") {
        node.className = value;
      } else if (key === "dataset") {
        Object.assign(node.dataset, value);
      } else if (key === "html") {
        node.innerHTML = value;
      } else if (key === "style" && typeof value === "object") {
        Object.assign(node.style, value);
      } else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (value === true) {
        node.setAttribute(key, "");
      } else {
        node.setAttribute(key, String(value));
      }
    }
  }
  appendChildren(node, children);
  return node;
}

function appendChildren(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) {
      appendChildren(node, child);
    } else if (child instanceof Node) {
      node.append(child);
    } else {
      node.append(document.createTextNode(String(child)));
    }
  }
}

/** Escape text for the rare case we must build an HTML string (never for source document text). */
export function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

let toastRoot = null;
function getToastRoot() {
  if (!toastRoot) toastRoot = document.getElementById("toast-root");
  return toastRoot;
}

/**
 * toast(message, opts)
 * opts: { type: "info"|"error"|"success", timeout: ms, actionLabel, onAction }
 */
export function toast(message, opts = {}) {
  const { type = "info", timeout = 4000, actionLabel, onAction } = opts;
  const root = getToastRoot();
  if (!root) return;

  const item = el("div", { class: `toast toast--${type}` },
    el("span", { class: "toast__msg" }, message),
  );
  if (actionLabel && typeof onAction === "function") {
    item.append(el("button", {
      class: "toast__action",
      type: "button",
      onClick: () => { onAction(); dismiss(); },
    }, actionLabel));
  }
  item.append(el("button", {
    class: "toast__close",
    type: "button",
    "aria-label": "閉じる",
    onClick: () => dismiss(),
  }, "×"));

  root.append(item);
  requestAnimationFrame(() => item.classList.add("toast--visible"));

  let timer = null;
  function dismiss() {
    if (timer) clearTimeout(timer);
    item.classList.remove("toast--visible");
    setTimeout(() => item.remove(), 200);
  }
  if (timeout > 0) timer = setTimeout(dismiss, timeout);
  return dismiss;
}

/** debounce(fn, wait) -> debounced function */
export function debounce(fn, wait = 250) {
  let timer = null;
  function debounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), wait);
  }
  debounced.cancel = () => clearTimeout(timer);
  return debounced;
}

/** fmtDate(iso) -> localized "YYYY/MM/DD HH:mm" (JST-friendly, uses browser locale) */
export function fmtDate(iso) {
  if (!iso) return "-";
  try {
    let s = iso;
    // Treat naive ISO strings (no timezone marker) as UTC per data_model.md.
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$/.test(s)) s = s.replace(" ", "T") + "Z";
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return iso;
  }
}

/** Flash an element yellow for ~2s, used by anchor resolution. */
export function flashElement(elm) {
  if (!elm) return;
  elm.classList.remove("flash-target");
  // force reflow so the animation restarts if already applied
  void elm.offsetWidth;
  elm.classList.add("flash-target");
  setTimeout(() => elm.classList.remove("flash-target"), 2100);
}

/**
 * openModal(contentNode, opts) -> { close }
 * Generic modal/dialog shell. opts: { onClose, closeOnBackdrop=true, labelledBy, wide }
 */
export function openModal(contentNode, opts = {}) {
  const { onClose, closeOnBackdrop = true, wide = false } = opts;
  const backdrop = el("div", { class: "modal-backdrop" });
  const dialog = el("div", { class: wide ? "modal modal--wide" : "modal", role: "dialog", "aria-modal": "true" }, contentNode);
  backdrop.append(dialog);
  document.body.append(backdrop);
  document.body.classList.add("no-scroll");

  function close() {
    document.removeEventListener("keydown", onKeydown);
    backdrop.remove();
    document.body.classList.remove("no-scroll");
    if (typeof onClose === "function") onClose();
  }
  function onKeydown(e) {
    if (e.key === "Escape") close();
  }
  document.addEventListener("keydown", onKeydown);
  if (closeOnBackdrop) {
    backdrop.addEventListener("mousedown", (e) => {
      if (e.target === backdrop) close();
    });
  }
  requestAnimationFrame(() => backdrop.classList.add("modal-backdrop--visible"));
  return { close, backdrop, dialog };
}

/** confirmDialog(message, opts) -> Promise<boolean> */
export function confirmDialog(message, opts = {}) {
  const { okLabel = "OK", cancelLabel = "キャンセル", detail = null } = opts;
  return new Promise((resolve) => {
    let settled = false;
    const body = el("div", { class: "confirm-dialog" },
      el("p", { class: "confirm-dialog__msg" }, message),
      detail ? el("p", { class: "confirm-dialog__detail" }, detail) : null,
      el("div", { class: "modal__actions" },
        el("button", { class: "btn", type: "button", onClick: () => finish(false) }, cancelLabel),
        el("button", { class: "btn btn--primary", type: "button", onClick: () => finish(true) }, okLabel),
      ),
    );
    const modal = openModal(body, { onClose: () => finish(false) });
    function finish(value) {
      if (settled) return;
      settled = true;
      resolve(value);
      modal.close();
    }
  });
}

/** Normalize whitespace for quote comparison (source_anchor_spec.md). */
export function normalizeWhitespace(str) {
  return String(str ?? "").replace(/\s+/g, " ").trim();
}

/**
 * Defensively normalize a SourceAnchor that may arrive either pre-parsed (object, as
 * /note and /questions return it) or as a raw JSON string (as /document's highlights[]
 * currently returns it - source_anchor_spec.md stores anchors as "JSON文字列" and not
 * every read path deserializes them before responding). Returns null on anything unusable.
 */
export function parseAnchor(raw) {
  if (!raw) return null;
  if (typeof raw === "object") return raw;
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * Defensively normalize a value that should be a string array (e.g. sources.authors,
 * which data_model.md defines as "JSON配列" - some read paths return it already parsed,
 * others return the raw JSON-encoded TEXT column value). Always returns an array.
 */
export function parseStringArray(raw) {
  if (Array.isArray(raw)) return raw;
  if (typeof raw === "string" && raw.trim()) {
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}
