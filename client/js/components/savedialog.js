// client/js/components/savedialog.js
// Save dialog: opens instantly with prefilled content, fires POST /api/knowledge/suggest
// in the background and applies the suggestion to the selects unless the user already
// changed them. Save should be a 2-click flow (open -> save).

import { api } from "../api.js";
import { el, openModal, toast } from "../util.js";
import { state, noteTemplateLabel, INFO_TYPE_LABEL, INFO_TYPE_OPTIONS } from "../state.js";

/**
 * openSaveDialog(opts)
 * opts: {
 *   sourceId, content, title (optional prefill), anchor (SourceAnchor|null),
 *   origin: "source_quote"|"llm"|"user"|"llm_edited",
 *   infoType (default guess), sectionKey (default guess), promptType (hint for suggest heuristic),
 *   questionId, answerId,
 *   lockDefaults: true -> infoType/sectionKey were an explicit user choice (e.g. the "+メモを追加"
 *     button under a specific section), so the async /knowledge/suggest reply must NOT override them.
 *     Default false: infoType/sectionKey are just starting guesses the suggestion may refine.
 *   onSaved(item) -> called immediately on successful save (optimistic local UI updates, e.g. "saved" chip)
 *   onViewRequested(item) -> called when the user clicks the toast's [表示] action (navigate + flash)
 * }
 */
export function openSaveDialog(opts) {
  const {
    sourceId, content = "", title: titleDefault = "", anchor = null,
    origin = "user", infoType: infoTypeDefault = null, sectionKey: sectionDefault = null,
    promptType = null, questionId = null, answerId = null, onSaved = null, onViewRequested = null,
    lockDefaults = false,
  } = opts;

  const sections = state.meta?.note_template || [];
  let userChangedSection = lockDefaults && !!sectionDefault;
  let userChangedInfoType = lockDefaults && !!infoTypeDefault;
  let userChangedTitle = false;
  let saving = false;

  const contentField = el("textarea", { rows: 6 }, content);
  const titleField = el("input", { type: "text", placeholder: "(任意) タイトル", value: titleDefault });
  const sectionSelect = el("select", {
    onChange: () => { userChangedSection = true; },
  }, ...sections.map((s) => el("option", { value: s.key }, s.label)));
  const infoTypeSelect = el("select", {
    onChange: () => { userChangedInfoType = true; },
  }, ...INFO_TYPE_OPTIONS.map((k) => el("option", { value: k }, INFO_TYPE_LABEL[k])));

  if (sectionDefault) sectionSelect.value = sectionDefault;
  if (infoTypeDefault) infoTypeSelect.value = infoTypeDefault;
  titleField.addEventListener("input", () => { userChangedTitle = true; });

  const saveBtn = el("button", { class: "btn btn--primary", type: "button", onClick: doSave }, "保存");
  const cancelBtn = el("button", { class: "btn", type: "button", onClick: () => modal.close() }, "キャンセル");

  const body = el("div", {},
    el("h2", {}, "ノートに保存"),
    el("div", { class: "field" }, el("label", {}, "保存する内容"), contentField),
    el("div", { class: "field" }, el("label", {}, "タイトル(任意)"), titleField),
    el("div", { class: "field" }, el("label", {}, "保存先セクション"), sectionSelect),
    el("div", { class: "field" }, el("label", {}, "情報種別"), infoTypeSelect),
    el("div", { class: "modal__actions" }, cancelBtn, saveBtn),
  );

  const modal = openModal(body);
  // Focus content for quick edits.
  setTimeout(() => contentField.focus(), 0);

  // Fire suggestion in background; apply only to fields the user hasn't touched yet.
  api.suggestKnowledge({ source_id: sourceId, content, prompt_type: promptType }).then((res) => {
    if (!res) return;
    if (res.section_key && !userChangedSection && sections.some((s) => s.key === res.section_key)) {
      sectionSelect.value = res.section_key;
    }
    if (res.info_type && !userChangedInfoType && INFO_TYPE_OPTIONS.includes(res.info_type)) {
      infoTypeSelect.value = res.info_type;
    }
    if (res.title && !userChangedTitle && !titleField.value) {
      titleField.value = res.title;
    }
  }).catch(() => { /* suggest never blocks save; ignore failures */ });

  async function doSave() {
    if (saving) return;
    const contentValue = contentField.value.trim();
    if (!contentValue) {
      toast("内容を入力してください", { type: "error" });
      contentField.focus();
      return;
    }
    saving = true;
    saveBtn.disabled = true;
    saveBtn.textContent = "保存中...";
    try {
      const res = await api.createKnowledge({
        source_id: sourceId,
        section_key: sectionSelect.value || sections[0]?.key || "misc",
        title: titleField.value.trim() || null,
        content: contentValue,
        origin,
        info_type: infoTypeSelect.value,
        anchor: anchor || null,
        question_id: questionId,
        answer_id: answerId,
      });
      const item = res.item;
      modal.close();
      if (typeof onSaved === "function") onSaved(item); // immediate: caller-side optimistic UI update
      toast("ノートに保存しました", {
        type: "success",
        actionLabel: onViewRequested ? "表示" : undefined,
        onAction: onViewRequested ? () => onViewRequested(item) : undefined,
      });
    } catch {
      // api.js already toasted the error
      saving = false;
      saveBtn.disabled = false;
      saveBtn.textContent = "保存";
    }
  }

  return modal;
}

// Convenience label used elsewhere for prompt_type -> section heuristic hint display, if needed.
export function sectionLabelFor(key) {
  return noteTemplateLabel(key);
}
