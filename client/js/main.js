// client/js/main.js
// Hash router (#/ = library, #/read/<id> = reader) + startup /api/meta + global
// cross-material search (dropdown in the header, per ui_spec.md).

import { api } from "./api.js";
import { el, debounce } from "./util.js";
import { setMeta, setPendingFocus } from "./state.js";
import * as library from "./views/library.js";
import * as reader from "./views/reader.js";

const appEl = document.getElementById("app");
const metaEl = document.getElementById("global-meta");
const searchInput = document.getElementById("global-search-input");
const searchResults = document.getElementById("global-search-results");

let currentView = null; // "library" | "reader"

const KIND_LABEL = { source: "資料", knowledge: "知識", question: "質問", answer: "回答" };

async function init() {
  try {
    const meta = await api.getMeta();
    setMeta(meta);
    if (metaEl) metaEl.textContent = `${meta.provider} / ${meta.model}`;
  } catch {
    if (metaEl) metaEl.textContent = "";
  }
  wireGlobalSearch();
  window.addEventListener("hashchange", route);
  route();
}

function parseRoute() {
  const hash = location.hash || "#/";
  const readMatch = hash.match(/^#\/read\/([^/?#]+)/);
  if (readMatch) return { name: "reader", sourceId: decodeURIComponent(readMatch[1]) };
  return { name: "library" };
}

let routeEpoch = 0;

// route() is async (fetches data before rendering); a rapid navigation (e.g. clicking
// a duplicate-dialog's "既存を開く" right before another hash change) can start a second
// route() before the first finishes. Guard with an epoch token so a superseded call's
// late-arriving render never clobbers what's currently on screen.
async function route() {
  const myEpoch = ++routeEpoch;
  const isCurrent = () => myEpoch === routeEpoch;
  const r = parseRoute();
  if (currentView === "reader") reader.unmount();
  if (!isCurrent()) return;
  appEl.textContent = "";
  if (r.name === "reader") {
    currentView = "reader";
    await reader.render(appEl, r.sourceId, isCurrent);
  } else {
    currentView = "library";
    await library.render(appEl, isCurrent);
  }
}

function wireGlobalSearch() {
  if (!searchInput || !searchResults) return;

  const runSearch = debounce(async () => {
    const q = searchInput.value.trim();
    if (!q) { hideResults(); return; }
    try {
      const res = await api.search(q, { silent: true });
      renderResults(res.results || []);
    } catch {
      hideResults();
    }
  }, 300);

  searchInput.addEventListener("input", runSearch);
  searchInput.addEventListener("focus", () => { if (searchInput.value.trim()) runSearch(); });
  document.addEventListener("mousedown", (e) => {
    if (e.target !== searchInput && !searchResults.contains(e.target)) hideResults();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideResults(); });
}

function hideResults() {
  searchResults.hidden = true;
  searchResults.textContent = "";
}

function renderResults(results) {
  searchResults.textContent = "";
  if (!results.length) {
    searchResults.append(el("div", { class: "search-dropdown__empty" }, "一致する結果がありません"));
  } else {
    for (const r of results) {
      searchResults.append(el("button", {
        class: "search-result", type: "button",
        onClick: () => goToResult(r),
      },
        el("span", { class: "search-result__kind" }, KIND_LABEL[r.kind] || r.kind),
        el("div", { class: "search-result__body" },
          el("div", { class: "search-result__title" }, r.source_title || ""),
          el("div", { class: "search-result__snippet" }, r.snippet || ""),
        ),
      ));
    }
  }
  searchResults.hidden = false;
}

function goToResult(r) {
  hideResults();
  searchInput.value = "";
  if (r.kind === "source") {
    location.hash = `#/read/${r.source_id}`;
    return;
  }
  setPendingFocus({ sourceId: r.source_id, kind: r.kind, refId: r.ref_id });
  const target = `#/read/${r.source_id}`;
  if (location.hash === target) {
    route(); // hash unchanged -> hashchange won't fire; re-render manually to consume the focus
  } else {
    location.hash = target;
  }
}

init();
