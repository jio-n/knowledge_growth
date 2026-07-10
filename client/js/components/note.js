// client/js/components/note.js
// ノートタブ: one_line_summary (inline editable), note_template sections of knowledge
// items with origin/info_type badges, hover actions (edit/move/verify/delete),
// "+ メモを追加", and the "Markdownで見る" export preview modal.

import { api } from "../api.js";
import { el, toast, confirmDialog, flashElement, openModal } from "../util.js";
import { marked } from "/vendor/marked.esm.js";
import { openSaveDialog } from "./savedialog.js";
import {
  state, ORIGIN_META, INFO_TYPE_LABEL, VERIFICATION_LABEL, VERIFICATION_CYCLE, noteTemplateLabel,
} from "../state.js";

let ctx = null; // { sourceId, resolveAnchor, switchTab }
let container = null;
let noteData = null;

export function mount(rootEl, sourceId, opts = {}) {
  ctx = { sourceId, resolveAnchor: opts.resolveAnchor || (() => {}), switchTab: opts.switchTab || (() => {}) };
  container = rootEl;
  container.textContent = "";
  load(opts.focusItemId || null);
}

export function unmount() {
  if (container) container.textContent = ""; // drop stale DOM so hidden panels don't linger
  ctx = null;
  container = null;
  noteData = null;
}

async function load(focusItemId) {
  if (!container) return;
  try {
    noteData = await api.getNote(ctx.sourceId);
  } catch {
    return;
  }
  if (!container) return;
  render();
  if (focusItemId) flashItem(focusItemId);
}

function render() {
  container.textContent = "";
  container.append(buildHeader());
  container.append(buildOneLineSummary());
  const sections = [...(noteData.sections || []), ...(noteData.extra_sections || [])];
  for (const section of sections) {
    container.append(renderSection(section));
  }
}

function buildHeader() {
  return el("div", { class: "note-header" },
    el("h3", {}, "理解ノート"),
    el("button", { class: "btn btn--sm", type: "button", onClick: openMarkdownPreview }, "Markdownで見る"),
  );
}

function buildOneLineSummary() {
  const source = noteData.source;
  const box = el("div", { class: "one-line-summary" });

  function renderView() {
    box.textContent = "";
    box.onclick = startEdit;
    box.append(source.one_line_summary || "(一言要約なし。クリックして入力)");
  }

  function startEdit() {
    box.onclick = null;
    box.textContent = "";
    const ta = el("textarea", {}, source.one_line_summary || "");
    box.append(
      ta,
      el("div", { class: "modal__actions" },
        el("button", { class: "btn btn--sm", type: "button", onClick: renderView }, "キャンセル"),
        el("button", { class: "btn btn--sm btn--primary", type: "button", onClick: save }, "保存"),
      ),
    );
    ta.focus();

    async function save() {
      const val = ta.value.trim();
      try {
        const res = await api.patchSource(ctx.sourceId, { one_line_summary: val });
        source.one_line_summary = res && res.source ? res.source.one_line_summary : val;
        toast("一言要約を更新しました", { type: "success" });
      } catch {
        // api.js already toasted
      }
      renderView();
    }
  }

  renderView();
  return el("div", { class: "field" }, el("label", {}, "一言要約"), box);
}

function renderSection(section) {
  const wrap = el("div", { class: "section-block", dataset: { sectionKey: section.key } });
  wrap.append(el("div", { class: "section-block__head" }, el("h3", {}, section.label || noteTemplateLabel(section.key))));
  if (!section.items || !section.items.length) {
    wrap.append(el("div", { class: "section-block__empty" }, "項目はまだありません"));
  } else {
    for (const item of section.items) wrap.append(renderItemCard(item));
  }
  wrap.append(el("button", {
    class: "btn btn--sm add-memo-btn", type: "button",
    onClick: () => addMemo(section.key),
  }, "+ メモを追加"));
  return wrap;
}

function renderItemCard(item) {
  const meta = ORIGIN_META[item.origin] || ORIGIN_META.user;
  const card = el("div", { class: "item-card", dataset: { itemId: item.id } });

  card.append(el("div", { class: "item-card__head" },
    el("span", { class: `badge ${meta.badgeClass}` }, meta.label),
    item.info_type ? el("span", { class: "info-type-label" }, INFO_TYPE_LABEL[item.info_type] || item.info_type) : null,
  ));
  if (item.title) card.append(el("div", { class: "item-card__title" }, item.title));

  const contentHost = el("div", { class: "item-card__content ai-content" });
  contentHost.innerHTML = marked.parse(item.content || "");
  card.append(contentHost);

  const footer = el("div", { class: "item-card__footer" });
  if (item.anchor) {
    footer.append(el("button", { class: "link-btn", type: "button", onClick: () => ctx.resolveAnchor(item.anchor) }, "📍 原文へ"));
  }
  if (item.question_id) {
    footer.append(el("button", {
      class: "link-btn", type: "button",
      onClick: () => ctx.switchTab("qa", { focusQuestionId: item.question_id }),
    }, "由来Q&Aを見る"));
  }
  if (item.verification && item.verification !== "unverified") {
    footer.append(el("span", {}, VERIFICATION_LABEL[item.verification]));
  }
  card.append(footer);

  card.append(el("div", { class: "item-card__actions" },
    el("button", { type: "button", title: "編集", onClick: () => startEditItem(item, contentHost) }, "編集"),
    buildMoveSectionSelect(item),
    el("button", {
      type: "button", title: "検証状態を切替",
      onClick: () => cycleVerification(item),
    }, VERIFICATION_LABEL[item.verification || "unverified"]),
    el("button", { type: "button", title: "削除", onClick: () => deleteItem(item) }, "削除"),
  ));

  return card;
}

function buildMoveSectionSelect(item) {
  const sections = state.meta?.note_template || [];
  const select = el("select", {
    title: "セクション移動",
    onClick: (e) => e.stopPropagation(),
    onChange: async (e) => {
      const newKey = e.target.value;
      if (newKey === item.section_key) return;
      try {
        await api.patchKnowledge(item.id, { section_key: newKey });
        toast("セクションを移動しました", { type: "success" });
        load();
      } catch {
        // api.js already toasted
      }
    },
  }, ...sections.map((s) => el("option", { value: s.key, selected: s.key === item.section_key || undefined }, s.label)));
  return select;
}

function startEditItem(item, contentHost) {
  contentHost.textContent = "";
  const ta = el("textarea", {}, item.content || "");
  contentHost.append(
    ta,
    el("div", { class: "modal__actions" },
      el("button", { class: "btn btn--sm", type: "button", onClick: () => render() }, "キャンセル"),
      el("button", { class: "btn btn--sm btn--primary", type: "button", onClick: save }, "保存"),
    ),
  );
  ta.focus();

  async function save() {
    const val = ta.value.trim();
    if (!val) { toast("内容を入力してください", { type: "error" }); return; }
    try {
      await api.patchKnowledge(item.id, { content: val });
      toast("更新しました", { type: "success" });
      load();
    } catch {
      // api.js already toasted
    }
  }
}

async function cycleVerification(item) {
  const idx = VERIFICATION_CYCLE.indexOf(item.verification || "unverified");
  const next = VERIFICATION_CYCLE[(idx + 1) % VERIFICATION_CYCLE.length];
  try {
    await api.patchKnowledge(item.id, { verification: next });
    load();
  } catch {
    // api.js already toasted
  }
}

async function deleteItem(item) {
  const ok = await confirmDialog("この項目を削除しますか?", {
    okLabel: "削除する",
    detail: item.title || (item.content || "").slice(0, 60),
  });
  if (!ok) return;
  try {
    await api.deleteKnowledge(item.id);
    toast("削除しました", { type: "success" });
    load();
  } catch {
    // api.js already toasted
  }
}

function addMemo(sectionKey) {
  openSaveDialog({
    sourceId: ctx.sourceId,
    content: "",
    anchor: null,
    origin: "user",
    infoType: "user_thought",
    sectionKey,
    promptType: null,
    lockDefaults: true, // the section was explicitly chosen by clicking "+ メモを追加" there
    onSaved: () => load(),
    onViewRequested: (item) => flashItem(item.id),
  });
}

export function flashItem(itemId) {
  if (!container) return;
  const cardEl = container.querySelector(`[data-item-id="${itemId}"]`);
  if (cardEl) {
    cardEl.scrollIntoView({ behavior: "smooth", block: "center" });
    flashElement(cardEl);
  }
}

async function openMarkdownPreview() {
  const body = el("div", {},
    el("h2", {}, "Markdownで見る"),
    el("div", { class: "md-preview" }, "読み込み中..."),
  );
  const modal = openModal(body, { wide: true });
  try {
    const text = await api.fetchExportMd(ctx.sourceId);
    const pre = body.querySelector(".md-preview");
    pre.textContent = text;
    body.append(el("div", { class: "modal__actions" },
      el("a", { class: "btn btn--sm", href: api.exportMdUrl(ctx.sourceId, 1), download: true }, "Markdownをダウンロード"),
      el("a", { class: "btn btn--sm", href: api.exportJsonUrl(ctx.sourceId), download: true }, "JSONダウンロード"),
      el("button", { class: "btn btn--sm btn--primary", type: "button", onClick: () => modal.close() }, "閉じる"),
    ));
  } catch {
    const pre = body.querySelector(".md-preview");
    pre.textContent = "エクスポートの取得に失敗しました。";
  }
}
