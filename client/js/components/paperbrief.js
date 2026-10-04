import { api } from "../api.js";
import { el } from "../util.js";
import { FIELD_LABELS, statusBadge, valueView, evidenceView } from "./brieffields.js";

let epoch = 0;
export function unmount() { epoch++; }
export async function mount(container, sourceId, { onEvidence, onPdf, onImport, isPdf }) {
  const token = ++epoch;
  container.replaceChildren(el("p", {}, "Paper Briefを読み込み中…"));
  try {
    const { paper_brief: brief } = await api.getPaperBrief(sourceId, { silent: true });
    if (token !== epoch) return;
    const actions = el("div", { class: "brief-actions" },
      el("button", { class: "btn btn--sm", type: "button", onClick: onPdf, disabled: !isPdf }, "原文PDFを見る"),
      el("button", { class: "btn btn--sm", type: "button", disabled: true, title: "日本語版Readerは今後対応" }, "日本語版で読む · 今後対応"),
      el("button", { class: "btn btn--sm", type: "button", onClick: onImport }, "ChatGPTから取り込む"));
    container.replaceChildren(el("h2", {}, "Paper Brief"), actions);
    if (!brief) {
      container.append(el("p", {}, "Paper Briefは未登録です。knowledge_growth用 .kgpack を取り込むと表示できます。"));
      return;
    }
    container.append(el("p", { class: "brief-muted" },
      "Import由来の解析と原文を区別して確認してください。statusは情報の記載状態で、verification（検証）とは別です。"));
    function fieldView(name, compact = false) {
      const f = brief.fields[name];
      const card = el("article", { class: "brief-field", dataset: { field: name } }, el("div", { class: "brief-field-heading" }, el("h4", {}, FIELD_LABELS[name] || name), f ? statusBadge(f.status) : null));
      if (!f) {
        card.append(el("p", { class: "brief-muted" }, "未登録（未報告・非該当とは異なります）"));
        return card;
      }
      card.append(valueView(name, f.value, { resolutions: f.evidence_resolution, onEvidence }),
        el("details", {}, el("summary", {}, compact ? "由来・Evidenceを確認" : "由来を確認"),
        el("p", { class: "brief-provenance" }, `origin: ${f.origin}${f.user_edited ? " · ユーザー編集済み" : ""} · verification: ${f.verification}`),
        el("p", { class: "brief-value" },
          `package: ${f.provenance.package_id ?? "—"} · generator: ${f.provenance.generator_label || f.provenance.generated_by || "—"} · imported: ${f.provenance.imported_at ?? "—"}`),
        compact ? evidenceView(f.evidence_resolution, { onEvidence }) : null));
      if (compact) {
        const states = [...new Set(f.evidence_resolution.map((e) => e.status))];
        card.append(el("p", { class: "brief-muted" }, `Evidence: ${states.join(" / ") || "なし"}${states.length && states.every((s) => s === "resolved") ? "" : " · 根拠未確定"}`));
        const resolved = f.evidence_resolution.find((e) => e.status === "resolved" && e.anchor);
        if (resolved && name !== "key_results") card.append(el("button", { class: "btn btn--sm", type: "button", onClick: () => onEvidence(resolved.anchor) }, "📍 原文を見る"));
      } else card.append(evidenceView(f.evidence_resolution, { onEvidence }));
      return card;
    }
    const quick = ["one_line_summary", "research_objective", "background", "problem", "target_task", "novelty", "key_results"];
    const model = ["model_family", "base_model", "learning_regimes", "shots", "adaptation_methods", "datasets", "metrics"];
    container.append(el("section", { class: "brief-section" }, el("h3", {}, "30秒Brief"), ...quick.map((name) => fieldView(name, true))),
      el("section", { class: "brief-section" }, el("h3", {}, "モデル・学習・評価"), ...model.map((name) => fieldView(name, true))),
      el("details", { class: "brief-structured" }, el("summary", {}, "Structured Brief · 詳細を開く"),
        ...Object.keys(FIELD_LABELS).map((name) => fieldView(name))));
  } catch {
    if (token !== epoch) return;
    container.replaceChildren(el("p", { role: "alert" }, "Paper Briefの取得に失敗しました。"),
      el("button", { class: "btn", type: "button", onClick: () => mount(container, sourceId, { onEvidence, onPdf, onImport, isPdf }) }, "再試行"));
  }
}
