// kgpack wizard: server-issued previews are the only source of import decisions.
import { api } from "../api.js";
import { el, parseStringArray } from "../util.js";
import { FIELD_LABELS, statusBadge, valueView, evidenceView } from "./brieffields.js";

const STEPS = ["Package選択", "Validate", "Source matching", "Import Preview", "User confirmation", "Commit", "完了"];
const ACTION_LABELS = { add: "追加", update: "既存fieldを更新", preserve_user: "ユーザーfieldを保持（上書き不可）", excluded: "Import対象外" };
const WARNINGS = {
  manual_source_selection_required: "対象PDFを明示的に選択してください。",
  evidence_needs_review: "未解決または曖昧なEvidenceがあります。確定根拠として扱われません。",
  field_conflicts: "既存fieldとの競合があります。更新内容とユーザーfieldの保持を確認してください。",
  duplicate_import: "package duplicate · このpackage IDまたは内容は取り込み済みです。再取り込みできません。",
  no_writable_fields: "取り込めるfieldがありません。除外設定を確認してください。",
  manual_source_selection: "ユーザーが手動選択したPDFです。別の論文でないことを確認してください。",
  optional_candidates_archived_only: "任意のNote / Q&Aはpackage履歴への保存のみです。ノート・対話には未反映です。",
  confirmed_requires_resolved_evidence: "Evidenceが確定していないためstatusをuncertainに変更します。",
  numeric_evidence_not_resolved: "結果のEvidenceが未確定のため数値のstatusをuncertainに変更します。",
};

export function openImportBridge({ onImported } = {}) {
  if (document.querySelector('.import-dialog')) return;
  const previousFocus = document.activeElement;
  let file = null, preview = null, sourceId = null, excludes = [], sources = [];
  let focusField = null, focusButton = null, focusId = null;
  let step = 0, busy = false, ready = false, error = null, mustRefresh = false, result = null;
  const content = el("div");
  const dialog = el("dialog", { class: "modal import-dialog", "aria-labelledby": "import-title" }, content);
  document.body.append(dialog);
  const close = () => { if (!busy) { dialog.close(); dialog.remove(); previousFocus?.focus(); } };
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.showModal();

  function button(label, handler, disabled = false, primary = false) {
    return el("button", { type: "button", class: `btn${primary ? " btn--primary" : ""}`, disabled: busy || disabled, onClick: handler }, label);
  }
  function identityView(identity, local = false) {
    const facts = [["Title", identity.title || "—"], ["Authors", parseStringArray(identity.authors).join("、") || "未報告"],
      ["Year", identity.year ?? "未報告"], ["DOI", identity.doi || "未報告"], ["arXiv", identity.arxiv_id || "未報告"],
      ["Source hash", (local ? identity.content_hash : identity.source_hash) || "未報告"]];
    return el("dl", { class: "brief-facts" }, ...facts.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
  }
  function warningView() {
    if (!preview) return null;
    const messages = preview.review_reasons.map((r) => WARNINGS[r] || r);
    for (const w of preview.warnings) messages.push(`${FIELD_LABELS[w.field] || w.field}${w.result_id ? ` / ${w.result_id}` : ""}: ${WARNINGS[w.reason] || w.reason}`);
    for (const c of preview.conflicts) messages.push(`${FIELD_LABELS[c.field] || c.field}: ${c.type === 'existing_field' ? 'existing field update · 既存値あり' : 'user-edited conflict · ユーザーfieldは上書き不可'} · ${c.policy}`);
    if (preview.source_matching.contradictions.length) messages.push("識別子に不一致があります。packageとlocal candidateのDOI / arXivを確認してください。");
    const totalEvidence = Object.values(preview.evidence_counts).reduce((sum, count) => sum + count, 0);
    return el("section", { class: "import-warnings" }, el("h3", {}, "Warning / conflict"),
      messages.length ? el("ul", {}, ...messages.map((m) => el("li", {}, m))) : el("p", {}, "警告なし"),
      el("p", {}, `Evidence解決率: ${preview.evidence_counts.resolved}/${totalEvidence}${totalEvidence ? ` (${Math.round(preview.evidence_counts.resolved / totalEvidence * 100)}%)` : "（Evidenceなし）"}`),
      el("p", {}, `Evidence: resolved ${preview.evidence_counts.resolved} / candidates ${preview.evidence_counts.candidates} / page_only ${preview.evidence_counts.page_only} / unresolved ${preview.evidence_counts.unresolved}`),
      el("details", {}, el("summary", {}, "全Evidenceを確認"), evidenceView(preview.evidence, { target: preview.target })));
  }
  function render() {
    const oldScroll = dialog.scrollTop;
    const focused = document.activeElement;
    if (dialog.contains(focused)) {
      focusField = focused?.closest(".import-field")?.dataset.field;
      focusButton = focused?.tagName === "BUTTON" ? focused.textContent : null;
      focusId = focused?.id || null;
    }
    content.replaceChildren(el("div", { class: "import-heading" }, el("h2", { id: "import-title" }, "ChatGPTから取り込む"), button("閉じる", close)),
      el("p", { class: "brief-muted" }, "knowledge_growth用 .kgpack を取り込みます。PDF原本や一般のZIPは選択できません。解析内容は原文で確認してください。"),
      el("ol", { class: "import-steps", "aria-label": "Import steps" }, ...STEPS.map((label, i) =>
        el("li", { class: i === step ? "active" : i < step ? "complete" : "", "aria-current": i === step ? "step" : null }, `${i + 1}. ${label}`))));
    if (file) content.append(el("p", {}, `Package: ${file.name}`));
    if (busy) content.append(el("p", { role: "status", "aria-live": "polite" }, `${STEPS[step]} 処理中…`));
    if (error) content.append(el("div", { class: "import-error", role: "alert" }, error,
      mustRefresh ? el("p", {}, "previewは保持しています。再生成後に対象・field・Evidenceを再確認してください。") : null));
    if (step === 0 || step === 1 && !preview) {
      const input = el("input", { type: "file", accept: ".kgpack", id: "kgpack-file", disabled: busy, onChange: (event) => {
        file = event.target.files[0] || null;
        preview = null; sourceId = null; excludes = []; ready = false; error = null; step = 0;
        if (file && !file.name.toLowerCase().endsWith(".kgpack")) { error = "knowledge_growth用 .kgpack を選択してください。"; file = null; }
        render();
      } });
      content.append(el("label", { for: "kgpack-file" }, ".kgpackファイルを選択"), input,
        el("div", { class: "modal__actions" }, button("Validateする", validate, !file, true)));
    }
    if (preview && step >= 2 && step <= 5) {
      const match = preview.source_matching;
      content.append(el("section", { class: "import-source" },
        el("h3", {}, `Source matching: ${match.status} · ${match.strength || "—"} · ${match.method || "照合なし"}`),
        el("h4", {}, "Packageの論文"), identityView(preview.manifest.source_identity),
        preview.target ? el("div", {}, el("h4", {}, "この論文に取り込みます"), identityView(preview.target, true)) :
          el("p", {}, match.status === "unmatched" ? "一致する論文がありません。既存PDFを選ぶか、閉じて先にPDFを登録してください。" : "候補を確認して対象PDFを明示選択してください。")));
      if (step === 2) {
        const candidates = el("div", { class: "import-local-candidates" }, el("h4", {}, `Local candidate（${match.candidates.length}件）`),
          ...match.candidates.map((s) => el("article", {}, identityView(s, true), button(`このPDFを選ぶ: ${s.title}`, () => selectSource(s.id)))));
        const select = el("select", { id: "import-source-select", disabled: busy, onChange: (e) => { if (e.target.value) selectSource(e.target.value); } },
          el("option", { value: "" }, "既存PDFから選択…"), ...sources.map((s) => el("option", { value: s.id, selected: sourceId === s.id }, `${s.title} · ${s.id}`)));
        content.append(candidates, el("label", { for: "import-source-select" }, "手動で対象PDFを選択"), select);
      }
      content.append(warningView());
      if (!ready && !busy) content.append(el("p", { role: "status" }, "選択設定のpreviewは未反映です。再生成が必要です。"));
      if (step === 4 || step === 5) content.append(confirmationView());
      if (step === 3) content.append(fieldPreview(true));
      if (step === 4 || step === 5) content.append(el("details", { class: "import-confirmation-fields" },
        el("summary", {}, "field差分・Evidenceをもう一度確認"), fieldPreview(false)));
      const actions = el("div", { class: "modal__actions" });
      if (step === 2) actions.append(button("Packageを選び直す", () => { step = 0; render(); }),
        button("Import Previewへ", () => { step = 3; render(); }, !preview.target || !ready, true));
      if (step === 3) actions.append(button("対象論文に戻る", () => { step = 2; render(); }),
        button("確認へ", () => { step = 4; render(); }, !ready || !preview.can_commit, true));
      if (step === 4 || step === 5) actions.append(button("Previewに戻る", () => { step = 3; render(); }),
        button("取り込む", commit, !ready || !preview.can_commit || mustRefresh, true));
      actions.append(button("previewを再生成", refreshPreview, false));
      content.append(actions);
    }
    if (step === 6) content.append(el("h3", {}, "取り込み完了"),
      el("p", {}, `${result.written_fields.length} fieldを保存しました。verificationはunverifiedのままです。`),
      button("Paper Briefを表示", () => {
        close();
        const hash = `#/read/${result.source_id}?tab=brief`;
        if (location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
        else location.hash = hash;
        onImported?.(result);
      }, false, true));
    const focusTarget = focusField ? content.querySelector(`.import-field[data-field="${focusField}"] input`) :
      focusButton ? [...content.querySelectorAll("button")].find((b) => b.textContent === focusButton && !b.disabled) :
      focusId ? content.querySelector(`#${focusId}`) : null;
    if (focusTarget && !focusTarget.disabled) focusTarget.focus({ preventScroll: true });
    dialog.scrollTop = oldScroll;
  }
  function fieldPreview(editable) {
    return el("section", { class: "import-fields" }, el("h3", {}, "Paper Brief field差分"),
      ...preview.fields.map((f) => {
        const protectedField = f.current && (f.current.origin === "user" || f.current.user_edited);
        const checkbox = el("input", { type: "checkbox", checked: !excludes.includes(f.name), disabled: busy || !editable,
          "aria-label": `${f.name} をImport対象にする`, onChange: (event) => {
            excludes = event.target.checked ? excludes.filter((n) => n !== f.name) : [...excludes, f.name];
            refreshPreview();
          } });
        const resolutions = [...new Map([...f.evidence_resolution, ...Object.values(f.result_evidence).flat()].map((e) => [e.evidence_id, e])).values()];
        return el("article", { class: "import-field", dataset: { field: f.name, action: f.action } },
          el("label", {}, checkbox, ` ${FIELD_LABELS[f.name] || f.name} · ${f.name}`),
          el("p", { class: "import-action" }, `${f.action} · ${ACTION_LABELS[f.action]}`),
          protectedField ? el("p", { class: "brief-preserved" }, "既存fieldはuser origin / user_editedです。Importで上書きできません。") : null,
          el("div", { class: "import-diff" }, el("div", {}, el("h4", {}, "Incoming value"), statusBadge(f.incoming.status),
            f.requested_status !== f.incoming.status ? el("p", {}, `要求status: ${f.requested_status} → ${f.incoming.status}`) : null,
            valueView(f.name, f.incoming.value, { resolutions, target: preview.target }),
            el("p", { class: "brief-provenance" }, `origin: ${f.incoming.origin} → 保存時 chatgpt_import · generator: ${f.incoming.provenance.generated_by || preview.manifest.generated_by}`)),
          el("div", {}, el("h4", {}, "Current value"), f.current ? [statusBadge(f.current.status), valueView(f.name, f.current.value, { resolutions: f.current.evidence_resolution, target: preview.target }),
            el("p", { class: "brief-provenance" }, `origin: ${f.current.origin} · user_edited: ${f.current.user_edited} · verification: ${f.current.verification}`)] : el("p", { class: "brief-muted" }, "既存値なし"))),
          evidenceView(resolutions, { target: preview.target }));
      }),
      el("details", {}, el("summary", {}, "任意Note / Q&A候補（ノート・対話には未反映）"),
        el("p", {}, "今回取り込むのはPaper Briefです。任意候補はpackage履歴に保存され、ノート・対話には反映されません。"),
        ...[...preview.knowledge_candidates, ...preview.qa_candidates].map((item) => el("article", {},
          el("h4", {}, item.title), el("p", { class: "brief-value" }, item.content || `${item.question}\n${item.answer}`))),
        !preview.knowledge_candidates.length && !preview.qa_candidates.length ? el("p", {}, "任意候補なし") : null));
  }
  function confirmationView() {
    const count = (action) => preview.fields.filter((f) => action.includes(f.action)).length;
    return el("section", { class: "import-confirmation" }, el("h3", {}, "取り込み内容の確認"),
      el("p", {}, `対象論文: ${preview.target?.title || "未選択"} · ${preview.target?.id || ""}`),
      el("dl", { class: "brief-facts" }, ...[
        ["Import field数", count(["add", "update"])], ["Excluded field数", count(["excluded"])],
        ["Preserved user field数", count(["preserve_user"])], ["Unresolved Evidence数（page_only含む）", preview.unresolved_count],
        ["Ambiguous Evidence数", preview.ambiguous_count],
      ].flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      el("p", {}, "「取り込む」を押すと、このpreviewの内容を保存します。未解決・候補のEvidenceを確定したり、verificationを昇格したりする操作ではありません。"));
  }
  function errorMessage(err) {
    const message = err.message || "不明なエラー";
    if (/duplicate/i.test(message)) return "package duplicate · 取り込み済みのpackageです。previewを再生成して確認してください。";
    if (/stale|expired|preview not found/i.test(message)) return "stale preview · 対象論文・Evidence・Briefが更新されたか、previewの期限が切れました。再生成して確認してください。";
    if (err.status === 422 || err.status === 413) return `kgpackの検証または選択設定に問題があります: ${message}`;
    return `処理に失敗しました。内容を保持しています。再試行できます: ${message}`;
  }
  async function validate() {
    if (busy || !file) return;
    busy = true; step = 1; error = null; render();
    try {
      await api.validateKgpack(file);
      preview = await api.previewKgpack(file);
      sources = (await api.listSources({}, { silent: true })).sources.filter((s) => s.type === "pdf");
      ready = true; step = 2;
    } catch (err) { error = errorMessage(err); step = 0; }
    finally { busy = false; render(); }
  }
  function selectSource(id) { sourceId = id; refreshPreview(); }
  async function refreshPreview() {
    if (busy) return;
    busy = true; ready = false; error = null; render();
    try {
      preview = await api.previewKgpack(file, { sourceId, excludeFields: excludes });
      ready = true; mustRefresh = false;
      if (step >= 4) step = 3; // Every changed snapshot needs a fresh confirmation.
    } catch (err) { error = errorMessage(err); }
    finally { busy = false; render(); }
  }
  async function commit() {
    if (busy || !ready || mustRefresh || !preview.can_commit) return;
    busy = true; step = 5; error = null; render();
    try { result = await api.commitKgpack(preview.preview_id); step = 6; }
    catch (err) {
      error = errorMessage(err);
      if (err.status === 409) { mustRefresh = true; ready = false; }
      step = 4;
    } finally { busy = false; render(); }
  }
  render();
  // Opening the primary action immediately proceeds to the kgpack chooser.
  content.querySelector('input[type="file"]').click();
}
