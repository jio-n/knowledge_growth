// client/js/state.js
// Client-side view state only. The server is the source of truth (per ui_spec.md);
// the exceptions are per-reader UI details (pane width / scroll / active tab) kept
// in localStorage under key `kg.reader.<sourceId>` (also spec-mandated).

export const state = {
  meta: null,          // GET /api/meta result: { provider, model, note_template, prompt_types }
  sourcesCache: [],     // last GET /api/sources result, used for library render + prev/next nav
  pendingFocus: null,   // { sourceId, tab, kind, refId } set by global search before hash navigation
};

export function setMeta(meta) {
  state.meta = meta;
}

export function noteTemplateLabel(key) {
  const entry = (state.meta?.note_template || []).find((s) => s.key === key);
  return entry ? entry.label : key;
}

export function promptTypeLabel(key) {
  const entry = (state.meta?.prompt_types || []).find((p) => p.key === key);
  return entry ? entry.label : key;
}

export function setSourcesCache(list) {
  state.sourcesCache = Array.isArray(list) ? list : [];
}

/** Neighbor id in the current library ordering (F-15: prev/next = library list order). */
export function neighborSourceId(currentId, direction) {
  const list = state.sourcesCache;
  const idx = list.findIndex((s) => s.id === currentId);
  if (idx === -1) return null;
  const targetIdx = direction === "next" ? idx + 1 : idx - 1;
  if (targetIdx < 0 || targetIdx >= list.length) return null;
  return list[targetIdx].id;
}

// ---- per-reader localStorage UI state ----

function readerKey(sourceId) {
  return `kg.reader.${sourceId}`;
}

const READER_DEFAULTS = {
  paneWidthPct: 55,   // left pane width, percent of the split area
  scrollTop: 0,
  activeTab: "summary", // summary | qa | note
  pdfMode: "pdf",       // pdf | block (only meaningful for type=pdf sources)
};

export function loadReaderUi(sourceId) {
  try {
    const raw = localStorage.getItem(readerKey(sourceId));
    if (!raw) return { ...READER_DEFAULTS };
    return { ...READER_DEFAULTS, ...JSON.parse(raw) };
  } catch {
    return { ...READER_DEFAULTS };
  }
}

export function saveReaderUi(sourceId, partial) {
  try {
    const current = loadReaderUi(sourceId);
    const next = { ...current, ...partial };
    localStorage.setItem(readerKey(sourceId), JSON.stringify(next));
  } catch {
    // localStorage unavailable (private mode etc.) - degrade silently
  }
}

// ---- pending focus (used by global search results / note & QA cross-links) ----

export function setPendingFocus(focus) {
  state.pendingFocus = focus;
}

export function consumePendingFocus(sourceId) {
  const f = state.pendingFocus;
  if (f && f.sourceId === sourceId) {
    state.pendingFocus = null;
    return f;
  }
  return null;
}

// ---- fixed vocabularies (requirements.md "情報種別・出所の語彙", ui_spec.md §13) ----

// origin -> { label, badgeClass }. badgeClass keys defined in css/app.css.
export const ORIGIN_META = {
  source_quote: { label: "原文", badgeClass: "badge--source-quote" },
  auto_extract: { label: "AI自動抽出", badgeClass: "badge--auto-extract" },
  llm: { label: "AI回答", badgeClass: "badge--llm" },
  llm_edited: { label: "AI回答・編集済", badgeClass: "badge--llm-edited" },
  user: { label: "自分", badgeClass: "badge--user" },
};

export const INFO_TYPE_LABEL = {
  fact: "事実",
  claim: "著者の主張",
  result: "実験結果",
  llm_summary: "AI要約",
  llm_interpretation: "AI解釈",
  user_thought: "考察",
  open_question: "未解決",
  idea: "アイデア",
  term: "用語",
  action: "アクション",
  translation: "翻訳",
};

export const READING_STATUS_LABEL = {
  unread: "未読",
  reading: "読書中",
  read: "読了",
  recheck: "要再確認",
};

export const VERIFICATION_LABEL = {
  unverified: "未検証",
  verified: "確認済",
  disputed: "要再確認",
};
export const VERIFICATION_CYCLE = ["unverified", "verified", "disputed"];

export const SOURCE_TYPE_LABEL = {
  pdf: "PDF",
  web: "Web",
  text: "テキスト",
  markdown: "Markdown",
};

// Selection popover quick-prompt buttons -> prompt_type (source_anchor_spec / ui_spec §selection).
export const QUICK_PROMPT_BUTTONS = [
  { promptType: "explain", label: "説明" },
  { promptType: "explain_simple", label: "初学者向け" },
  { promptType: "detail", label: "詳しく" },
  { promptType: "critique", label: "批判的に" },
  { promptType: "apply", label: "応用を考える" },
];

// info_type list offered when creating/editing a knowledge item.
export const INFO_TYPE_OPTIONS = Object.keys(INFO_TYPE_LABEL);
