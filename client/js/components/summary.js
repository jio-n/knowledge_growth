// client/js/components/summary.js
// 概要タブ: analysis_status polling (pending/running -> 3s poll), auto_extract items,
// bibliographic metadata + link to the original file, 再解析 action.

import { api } from "../api.js";
import { el, confirmDialog, toast, fmtDate, parseStringArray } from "../util.js";
import { marked } from "/vendor/marked.esm.js";
import { ORIGIN_META, INFO_TYPE_LABEL, noteTemplateLabel, SOURCE_TYPE_LABEL } from "../state.js";

let ctx = null; // { sourceId }
let container = null;
let pollTimer = null;

export function mount(rootEl, sourceId) {
  ctx = { sourceId };
  container = rootEl;
  container.textContent = "";
  load();
}

export function unmount() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (container) container.textContent = ""; // drop stale DOM so hidden panels don't linger
  ctx = null;
  container = null;
}

async function load() {
  if (!container) return;
  let source;
  try {
    const res = await api.getSource(ctx.sourceId);
    source = res.source;
  } catch {
    return;
  }
  if (!container) return; // unmounted while awaiting

  if (source.analysis_status === "pending" || source.analysis_status === "running") {
    renderPending(source);
    schedulePoll();
    return;
  }
  if (source.analysis_status === "error") {
    renderError(source);
    return;
  }
  renderDone(source);
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(load, 3000);
}

function renderPending() {
  container.textContent = "";
  container.append(
    el("div", { class: "summary-status" },
      el("div", { class: "spinner spinner--lg" }),
      el("div", { class: "summary-status__text" }, "構造化抽出を実行中です...(登録直後は数十秒かかることがあります)"),
    ),
  );
}

function renderError(source) {
  container.textContent = "";
  container.append(
    el("div", { class: "summary-error" },
      el("div", {}, `構造化抽出に失敗しました${source.analysis_error ? `: ${source.analysis_error}` : ""}`),
    ),
    el("button", { class: "btn", type: "button", onClick: () => reanalyze() }, "再実行"),
  );
  appendMeta(source);
}

async function renderDone(source) {
  container.textContent = "";

  const toolbar = el("div", { class: "summary-toolbar" },
    el("h3", {}, "構造化抽出結果(AI自動抽出)"),
    el("button", { class: "btn btn--sm", type: "button", onClick: () => reanalyze() }, "再解析"),
  );
  container.append(toolbar);

  let note;
  try {
    const res = await api.getNote(ctx.sourceId);
    note = res;
  } catch {
    note = null;
  }
  if (!container) return;

  const sections = [...(note?.sections || []), ...(note?.extra_sections || [])];
  const autoItems = sections
    .map((s) => ({ section: s, items: (s.items || []).filter((it) => it.origin === "auto_extract") }))
    .filter((g) => g.items.length > 0);

  if (!autoItems.length) {
    container.append(el("div", { class: "section-block__empty" }, "自動抽出された項目はありません。"));
  } else {
    for (const group of autoItems) {
      container.append(renderAutoSection(group.section, group.items));
    }
  }

  appendMeta(source);
}

function renderAutoSection(section, items) {
  const wrap = el("div", { class: "summary-section" },
    el("h3", {}, section.label || noteTemplateLabel(section.key)),
  );
  for (const item of items) {
    const meta = ORIGIN_META[item.origin] || ORIGIN_META.auto_extract;
    const content = el("div", { class: "item-card__content ai-content" });
    content.innerHTML = marked.parse(item.content || "");
    wrap.append(
      el("div", { class: "item-card" },
        el("div", { class: "item-card__head" },
          el("span", { class: `badge ${meta.badgeClass}` }, meta.label),
          item.info_type ? el("span", { class: "info-type-label" }, INFO_TYPE_LABEL[item.info_type] || item.info_type) : null,
        ),
        item.title ? el("div", { class: "item-card__title" }, item.title) : null,
        content,
      ),
    );
  }
  return wrap;
}

function appendMeta(source) {
  const rows = [];
  rows.push(row("種別", SOURCE_TYPE_LABEL[source.type] || source.type));
  const authors = parseStringArray(source.authors);
  if (authors.length) rows.push(row("著者", authors.join("、")));
  if (source.year) rows.push(row("年", source.year));
  if (source.venue) rows.push(row("媒体", source.venue));
  if (source.doi) rows.push(row("DOI", source.doi));
  if (source.arxiv_id) rows.push(row("arXiv", source.arxiv_id));
  if (source.url) rows.push(row("URL", el("a", { href: source.url, target: "_blank", rel: "noopener" }, source.url)));
  rows.push(row("登録日時", fmtDate(source.created_at)));

  const fileLink = el("a", { href: api.fileUrl(source.id), target: "_blank", rel: "noopener" }, "原本を開く");

  container.append(
    el("div", { class: "meta-table" },
      ...rows,
      el("div", {}, fileLink),
    ),
  );
}

function row(label, value) {
  return el("div", {}, el("span", { class: "k" }, label), value);
}

async function reanalyze() {
  const ok = await confirmDialog("構造化抽出を再実行しますか?", {
    okLabel: "再解析する",
    detail: "AI自動抽出の項目のみ置き換えられます。あなたが保存した項目(原文引用・AI回答保存・自分のメモ)は残ります。",
  });
  if (!ok) return;
  try {
    await api.reanalyzeSource(ctx.sourceId);
    toast("再解析を開始しました", { type: "success" });
    load();
  } catch {
    // api.js already toasted
  }
}
