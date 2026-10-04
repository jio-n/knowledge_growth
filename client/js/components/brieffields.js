// Presentation only: field values/statuses and Evidence returned by the backend.
import { el } from "../util.js";

export const FIELD_LABELS = {
  one_line_summary: "一言要約", research_objective: "研究目的", background: "背景", problem: "問題",
  target_task: "対象タスク", novelty: "新規性", key_results: "Key Results", paper_type: "Paper Type",
  target_domain: "Target domain", model_family: "使用モデル / Model family", base_model: "Base model",
  backbones: "Backbone", modalities: "Modalities", trainable_parts: "Trainable parts", frozen_parts: "Frozen parts",
  learning_regimes: "Learning regimes", shots: "Shots", supervision: "Supervision", adaptation_methods: "Adaptation methods",
  datasets: "Datasets", evaluation_settings: "Evaluation setting", metrics: "Metrics", baselines: "Baselines",
  architecture_summary: "Architecture", architecture_components: "Architecture components", architecture_data_flow: "Data flow",
  important_figures: "Important figures", important_tables: "Important tables", limitations: "Limitations",
  failure_cases: "Failure cases", reproducibility: "Reproducibility", inputs: "Inputs", outputs: "Outputs",
  proposed_method: "提案手法", model_size: "Model size", pretraining: "Pretraining", ablation: "Ablation",
  inference_requirements: "Inference requirements", training_requirements: "Training requirements",
  suggested_reading_order: "おすすめ読書順",
};
const STATUS_LABELS = {
  confirmed: "原文に明示", derived: "要約・統合", uncertain: "要確認",
  not_reported: "未報告", not_applicable: "非該当",
};
export function statusBadge(status) {
  return el("span", { class: `brief-status brief-status--${status}` }, `${status} · ${STATUS_LABELS[status] || status}`);
}
export function valueView(name, value, { resolutions = [], onEvidence, target } = {}) {
  if (value === null || value === undefined) return el("p", { class: "brief-muted" }, "値なし（状態ラベルを参照）");
  if (name === "key_results") {
    return el("div", { class: "brief-results" }, ...value.map((r) => el("article", { class: "brief-result", dataset: { resultId: r.id } },
      el("strong", {}, r.dataset),
      el("dl", { class: "brief-facts" }, ...[ ["Task", r.task], ["Setting", r.setting], ["Metric", r.metric],
        ["Score", `${r.score}${r.unit}`], ["Split", r.split ?? "未報告"], ["Comparison / Baseline", r.comparison ?? "未報告"]
      ].flatMap(([label, text]) => [el("dt", {}, label), el("dd", {}, text)])),
      statusBadge(r.status),
      evidenceView(resolutions.filter((e) => r.evidence.includes(e.evidence_id)), { onEvidence, target }),
    )));
  }
  if (Array.isArray(value)) return value.length
    ? el("ul", { class: "brief-values" }, ...value.map((v) => el("li", {}, v)))
    : el("p", { class: "brief-muted" }, "値なし（空のリスト）");
  if (typeof value === "object") return el("dl", { class: "brief-facts" },
    ...Object.entries(value).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v ?? "未報告")]));
  return el("p", { class: "brief-value" }, value);
}

export function readerEvidenceUrl(sourceId, anchor, kind = "resolved") {
  const params = new URLSearchParams({ evidence: JSON.stringify({ anchor, kind }) });
  return `#/read/${encodeURIComponent(sourceId)}?${params}`;
}
function evidenceControl(label, anchor, kind, { onEvidence, target }) {
  if (onEvidence) return el("button", { class: "btn btn--sm", type: "button", onClick: () => onEvidence(anchor, kind) }, label);
  if (target) return el("a", { class: "btn btn--sm", href: readerEvidenceUrl(target.id, anchor, kind), target: "_blank", rel: "noopener" }, `${label} ↗`);
  return null;
}
export function evidenceView(resolutions, options = {}) {
  if (!resolutions.length) return el("p", { class: "brief-muted" }, "Evidenceなし · 根拠未確定");
  return el("div", { class: "brief-evidence" }, ...resolutions.map((e) => {
    const labels = { resolved: "resolved · 解決済み", candidates: "candidates · 候補あり / 根拠未確定",
      page_only: "page_only · ページのみ / 根拠未確定", unresolved: "unresolved · 未解決 / 根拠未確定" };
    const row = el("div", { class: `evidence-row evidence-row--${e.status}`, dataset: { evidenceStatus: e.status } },
      el("span", { class: "evidence-state" }, `${e.evidence_id} · ${labels[e.status] || e.status}`));
    if (e.status === "resolved" && e.anchor) row.append(evidenceControl("📍 原文を見る", e.anchor, "resolved", options));
    if (e.status === "page_only") row.append(evidenceControl(`p.${e.page} を見る（根拠未確定）`, { page: e.page }, "page_only", options));
    const selector = e.portable || {};
    row.append(el("details", {}, el("summary", {}, "Evidence参照を確認"),
      el("p", { class: "brief-value" }, [selector.page ? `p.${selector.page}` : null, selector.heading,
        selector.figure_label, selector.table_label, selector.quote].filter(Boolean).join(" · ") || "参照情報なし")));
    if (e.status === "candidates") {
      row.append(el("p", { class: "brief-muted" }, "候補を見る操作は閲覧のみです。保存済みEvidenceは確定されません。"));
      row.append(el("details", { class: "evidence-candidates" }, el("summary", {}, `候補を見る（${e.candidates.length}件）`),
        ...e.candidates.map((b, i) => el("article", {},
          el("p", { class: "brief-value" }, `候補 ${i + 1} · p.${b.page ?? "?"} · ${b.text}`),
          evidenceControl(`候補 ${i + 1} の原文を見る`, {
            sourceVersion: b.version_id, blockId: b.id, blockIdx: b.idx, page: b.page, bbox: b.bbox,
          }, "candidate", options),
        ))));
    }
    return row;
  }));
}
