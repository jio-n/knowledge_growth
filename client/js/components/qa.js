// client/js/components/qa.js
// 対話タブ (Q&A tab): chronological question/answer cards, free-text composer,
// quick-prompt submission entry points used by selection.js.

import { api } from "../api.js";
import { el, escapeHtml, flashElement } from "../util.js";
import { openSaveDialog } from "./savedialog.js";
import { marked } from "/vendor/marked.esm.js";
import { promptTypeLabel } from "../state.js";

let ctx = null; // { sourceId, resolveAnchor, switchTab }
let rootContainer = null;
let listEl = null;
let composerTextarea = null;
let composerSendBtn = null;
let replyContextEl = null;
let pendingReplyContext = null; // { anchor, selectionText } prefilled from selection.js / "further question"
let questionsCache = [];
let answerRegistry = []; // [{ contentEl, saveSelBtn }] for the shared selectionchange handler

export function mount(container, sourceId, opts = {}) {
  ctx = { sourceId, resolveAnchor: opts.resolveAnchor || (() => {}), switchTab: opts.switchTab || (() => {}) };
  rootContainer = container;
  container.textContent = "";
  container.style.overflowY = "hidden";
  container.style.padding = "0";
  container.style.gap = "0";

  listEl = el("div", { class: "qa-list", style: { flex: "1", overflowY: "auto", padding: "var(--space-4)", display: "flex", flexDirection: "column", gap: "var(--space-4)" } });
  const composer = buildComposer();
  const root = el("div", { style: { display: "flex", flexDirection: "column", height: "100%", minHeight: "0" } }, listEl, composer);
  container.append(root);

  pendingReplyContext = null;
  load(opts.focusQuestionId || null);
}

export function unmount() {
  if (rootContainer) rootContainer.textContent = ""; // drop stale DOM so hidden panels don't linger
  ctx = null;
  rootContainer = null;
  listEl = null;
  answerRegistry = [];
}

function truncate(str, n) {
  const s = str || "";
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

async function load(focusQuestionId) {
  if (!listEl) return;
  listEl.textContent = "";
  listEl.append(el("div", { class: "qa-loading-card" }, el("div", { class: "spinner" }), "読み込み中..."));
  try {
    const res = await api.listQuestions(ctx.sourceId);
    questionsCache = res.questions || [];
    renderList();
    if (focusQuestionId) flashQuestion(focusQuestionId);
  } catch {
    if (listEl) listEl.textContent = "";
  }
}

function renderList() {
  if (!listEl) return;
  listEl.textContent = "";
  answerRegistry = [];
  if (!questionsCache.length) {
    listEl.append(el("div", { class: "section-block__empty" }, "まだ質問はありません。原文を選択するか、下の入力欄から質問できます。"));
    return;
  }
  for (const q of questionsCache) {
    listEl.append(renderQuestionCard(q));
  }
}

function renderQuestionCard(q) {
  const card = el("div", { class: "qa-card", dataset: { questionId: q.id } });
  if (q.selection_text || q.anchor) {
    card.append(el("button", {
      class: "qa-quote-chip", type: "button",
      onClick: () => ctx.resolveAnchor(q.anchor),
    }, `📍 ${truncate(q.selection_text || (q.anchor && q.anchor.quote) || "", 90)}`));
  }
  const qtext = q.question_text && q.question_text.trim() ? q.question_text : promptTypeLabel(q.prompt_type);
  card.append(el("div", { class: "qa-question__text" }, qtext));

  for (const a of (q.answers || [])) {
    card.append(renderAnswer(q, a));
  }
  return card;
}

function renderAnswer(q, a) {
  const wrap = el("div", { class: "qa-answer", dataset: { answerId: a.id } });
  const contentEl = el("div", { class: "ai-content" });
  contentEl.innerHTML = marked.parse(a.content || "");

  const badges = el("div", { class: "qa-answer__badges" },
    el("span", { class: "badge badge--llm" }, "AI回答"),
    a.model ? el("span", { class: "qa-answer__model" }, `/ ${a.model}`) : null,
    a.saved ? el("span", { class: "qa-saved-chip" }, "保存済→ノート") : null,
  );

  const saveSelBtn = el("button", {
    class: "btn btn--sm", type: "button", disabled: true,
    onClick: () => saveAnswer(q, a, "selection"),
  }, "選択部分を保存");
  const saveAllBtn = el("button", {
    class: "btn btn--sm", type: "button",
    onClick: () => saveAnswer(q, a, "all"),
  }, "全体を保存");
  const replyBtn = el("button", {
    class: "btn btn--sm btn--ghost", type: "button",
    onClick: () => startReply(q, a),
  }, "この回答にさらに質問");

  wrap.append(badges, contentEl, el("div", { class: "qa-answer__actions" }, saveAllBtn, saveSelBtn, replyBtn));
  answerRegistry.push({ contentEl, saveSelBtn });

  function saveAnswer(question, answer, mode) {
    let content = answer.content;
    if (mode === "selection") {
      const sel = window.getSelection();
      const text = sel && sel.toString().trim();
      if (text && contentEl.contains(sel.getRangeAt(0).commonAncestorContainer)) content = text;
    }
    openSaveDialog({
      sourceId: ctx.sourceId,
      content,
      anchor: question.anchor || null,
      origin: "llm",
      infoType: null,
      sectionKey: null,
      promptType: question.prompt_type,
      questionId: question.id,
      answerId: answer.id,
      onSaved: () => {
        answer.saved = true;
        renderList();
      },
      onViewRequested: (item) => ctx.switchTab("note", { flashId: item.id }),
    });
  }

  return wrap;
}

// One shared listener (module lifetime) drives "選択部分を保存" enablement for all
// currently rendered answers - avoids per-card listener leaks across re-renders.
document.addEventListener("selectionchange", () => {
  if (!answerRegistry.length) return;
  const sel = window.getSelection();
  const hasSel = !!(sel && !sel.isCollapsed && sel.rangeCount > 0 && sel.toString().trim());
  const range = hasSel ? sel.getRangeAt(0) : null;
  for (const { contentEl, saveSelBtn } of answerRegistry) {
    if (!contentEl.isConnected) continue;
    saveSelBtn.disabled = !(hasSel && contentEl.contains(range.commonAncestorContainer));
  }
});

function startReply(question, answer) {
  pendingReplyContext = {
    anchor: question.anchor || null,
    selectionText: `(前回の回答について) ${truncate(answer.content, 120)}`,
  };
  renderReplyContext();
  if (composerTextarea) composerTextarea.focus();
}

function renderReplyContext() {
  if (!replyContextEl) return;
  replyContextEl.textContent = "";
  if (!pendingReplyContext) { replyContextEl.hidden = true; return; }
  replyContextEl.hidden = false;
  replyContextEl.append(
    el("span", {}, `↪ ${truncate(pendingReplyContext.selectionText, 60)}`),
    el("button", { type: "button", onClick: () => { pendingReplyContext = null; renderReplyContext(); } }, "×"),
  );
}

function buildComposer() {
  replyContextEl = el("div", { class: "qa-reply-context", hidden: true });
  composerTextarea = el("textarea", { placeholder: "資料全体について質問する(自由入力)", rows: 2 });
  composerSendBtn = el("button", { class: "btn btn--primary", type: "button", onClick: submitFree }, "送信");
  composerTextarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitFree(); }
  });
  return el("div", { class: "qa-composer" },
    replyContextEl,
    el("div", { class: "qa-composer__row" }, composerTextarea, composerSendBtn),
  );
}

async function submitFree() {
  const text = composerTextarea.value.trim();
  if (!text) { composerTextarea.focus(); return; }
  const anchor = pendingReplyContext ? pendingReplyContext.anchor : null;
  const selectionText = pendingReplyContext ? pendingReplyContext.selectionText : "";
  pendingReplyContext = null;
  renderReplyContext();
  composerTextarea.value = "";
  await submitQuestion({ anchor, selectionText, promptType: "free", questionText: text });
}

/** Called by selection.js "質問する" action: switches into free-input mode with a quote prefilled. */
export function prefillFreeQuestion({ anchor, selectionText }) {
  pendingReplyContext = { anchor, selectionText: selectionText || "" };
  renderReplyContext();
  if (composerTextarea) composerTextarea.focus();
}

/** Called by selection.js quick-prompt actions (説明/初学者向け/詳しく/批判的に/応用). */
export async function submitQuickQuestion({ anchor, selectionText, promptType }) {
  await submitQuestion({ anchor, selectionText, promptType, questionText: "" });
}

async function submitQuestion({ anchor, selectionText, promptType, questionText }) {
  if (!listEl) return;
  setComposerLocked(true);
  const loadingCard = el("div", { class: "qa-loading-card" }, el("div", { class: "spinner" }), "AIが回答を作成しています...");
  listEl.append(loadingCard);
  listEl.scrollTop = listEl.scrollHeight;

  const payload = { anchor: anchor || null, selection_text: selectionText || "", prompt_type: promptType, question_text: questionText || "" };
  try {
    const res = await api.askQuestion(ctx.sourceId, payload, { silent: true });
    questionsCache.push(res.question);
    renderList();
    listEl.scrollTop = listEl.scrollHeight;
  } catch (err) {
    loadingCard.remove();
    listEl.append(renderErrorCard(err, payload));
  } finally {
    setComposerLocked(false);
  }
}

function renderErrorCard(err, payload) {
  const status = err && err.status;
  const message = status === 502
    ? "AIサーバーへの接続に失敗しました(502)。しばらくしてから再試行してください。"
    : `質問の送信に失敗しました: ${escapeHtml(err && err.message ? err.message : "unknown error")}`;
  const card = el("div", { class: "qa-error-card" },
    el("span", {}, message),
    el("button", {
      class: "btn btn--sm", type: "button",
      onClick: async (e) => {
        card.remove();
        await submitQuestion({
          anchor: payload.anchor, selectionText: payload.selection_text,
          promptType: payload.prompt_type, questionText: payload.question_text,
        });
      },
    }, "再試行"),
  );
  return card;
}

function setComposerLocked(locked) {
  if (composerTextarea) composerTextarea.disabled = locked;
  if (composerSendBtn) composerSendBtn.disabled = locked;
}

/** Used by note.js "由来Q&Aリンク" to jump to + flash a specific question card. */
export function flashQuestion(questionId) {
  if (!listEl) return;
  const cardEl = listEl.querySelector(`[data-question-id="${questionId}"]`);
  if (cardEl) {
    cardEl.scrollIntoView({ behavior: "smooth", block: "center" });
    flashElement(cardEl);
  }
}
