import { api } from "../api.js";
import { el } from "../util.js";
import { FIELD_LABELS, statusBadge, valueView, evidenceView } from "./brieffields.js";

const ACTIONS = { generated_update: "generated update · AI生成を更新", preserve_user: "preserve user · ユーザー編集を保持",
  new_field: "new field · 新規", unchanged: "unchanged · 変更なし" };

export function mountGeneration(host, sourceId, { brief, isPdf, onImport, onEvidence, onSaved, isCurrent }) {
  let job = { state: "not_generated" }, ready = false, busy = false, error = "", signature = "";
  let timer = null, polling = false, stale = false;
  const status = el("p", { role: "status", "aria-live": "polite", class: "brief-generation-state" }, "生成状態を確認中…");
  const controls = el("div", { class: "brief-actions" });
  const review = el("div", { class: "brief-generation-review" });
  host.append(status, controls, review);

  function render() {
    if (!isCurrent()) return;
    const running = job.state === "generating";
    const labels = { not_generated: brief ? "完了 · 保存済みBrief" : "未生成", generating: "生成中 · 原文PDFを引き続き閲覧できます",
      preview: "抽出完了 · 保存前に確認してください", completed: "完了", failed: "生成に失敗 · 既存Briefを保持しました" };
    status.textContent = error || labels[job.state] || "未生成";
    const generateLabel = !brief ? "Paper Briefを生成" : Object.values(brief.fields).some(f => f.origin === "user" || f.user_edited)
      ? "ユーザー編集を保持して再生成" : "Paper Briefを再生成";
    controls.replaceChildren(el("button", { type: "button", class: "btn btn--sm", disabled: busy || running || !ready || !isPdf,
      onClick: async () => {
        busy = true; error = ""; stale = false; render();
        try { job = await api.generatePaperBrief(sourceId); signature = ""; }
        catch (e) { error = e.status === 503 ? "AI未接続" : "生成の開始に失敗しました。再試行できます。"; }
        finally { busy = false; render(); await poll(); }
      } }, generateLabel));
    if (!ready) {
      status.textContent = error || `${labels[job.state] || "未生成"} · AI未接続`;
      controls.append(el("button", { type: "button", class: "btn btn--sm", onClick: () => window.dispatchEvent(new Event("kg-ai-connect")) }, "ChatGPTで接続"),
        el("button", { type: "button", class: "btn btn--sm", onClick: onImport }, "kgpackから取り込む"));
    }
    review.replaceChildren();
    if (job.state !== "preview" || !job.preview) return;
    const preview = job.preview;
    review.append(el("h3", {}, preview.mode === "initial" ? "初回生成 preview" : preview.mode === "regenerate_preserve_user" ? "ユーザー編集を保持した再生成 preview" : "再生成 preview"),
      el("p", {}, "AI生成の内容と根拠を確認してから保存してください。ユーザー編集済み項目は保持します。"));
    const coverage = preview.context_coverage.extraction;
    if (coverage.omitted_blocks || coverage.truncated_blocks) review.append(el("p", { class: "brief-muted" },
      `抽出context: ${coverage.sent_blocks}/${coverage.total_blocks} blocks。省略・短縮があるため、未報告は送信context内の記載状態です。`));
    review.append(el("details", { class: "brief-generation-diff", open: true }, el("summary", {}, "field差分・Evidenceを確認"),
      ...preview.fields.map(f => el("article", { class: "brief-field", dataset: { field: f.name, action: f.action } },
        el("h4", {}, FIELD_LABELS[f.name] || f.name), el("p", {}, ACTIONS[f.action]),
        f.current ? el("details", {}, el("summary", {}, "現在の値"), valueView(f.name, f.current.value)) : null,
        statusBadge(f.incoming.status), valueView(f.name, f.incoming.value, { resolutions: f.incoming.evidence_resolution, onEvidence }),
        evidenceView(f.incoming.evidence_resolution, { onEvidence })))));
    const confirmed = el("input", { type: "checkbox", disabled: busy });
    const save = el("button", { type: "button", class: "btn btn--primary", disabled: true, onClick: async () => {
      busy = true; error = ""; render();
      try { await api.commitBriefGeneration(sourceId, job.generation_id); if (isCurrent()) onSaved(); }
      catch (e) { stale = e.status === 409; error = stale ? "previewが古くなりました。再生成して確認してください。" : "保存に失敗しました。確認して再試行できます。"; }
      finally { busy = false; render(); }
    } }, "確認したBriefを保存");
    confirmed.addEventListener("change", () => { save.disabled = !confirmed.checked || busy || stale || !preview.can_commit; });
    review.append(el("label", {}, confirmed, "差分とEvidenceを確認しました"), save);
  }
  async function poll() {
    if (!isCurrent() || polling) return;
    clearTimeout(timer);
    polling = true;
    try {
      const results = await Promise.allSettled([api.getBriefGeneration(sourceId), fetch("/api/ai/status").then(r => {
        if (!r.ok) throw new Error("status unavailable"); return r.json();
      })]);
      if (!isCurrent()) return;
      if (results[0].status === "fulfilled") job = results[0].value;
      else error = "生成状態を取得できません。再試行できます。";
      ready = results[1].status === "fulfilled" && results[1].value.state === "ready" && results[1].value.capabilities?.inference;
      const next = JSON.stringify([job.state, job.generation_id, ready, error]);
      if (next !== signature) { signature = next; render(); }
    } finally { polling = false; if (isCurrent()) timer = setTimeout(poll, job.state === "generating" ? 800 : 3000); }
  }
  poll();
}
