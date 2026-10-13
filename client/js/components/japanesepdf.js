// Lifecycle UI only. The server owns safety, TranslationUnits and derived files.
import { api } from "../api.js";
import { el, toast } from "../util.js";

const REASONS = {
  visual_or_equation: "図表・数式", math_or_symbols: "数式・記号を含む",
  image_drawing_table_or_annotation: "画像・線・表・注釈と接近／交差",
  missing_geometry: "原文座標なし", overlapping_or_ambiguous_text: "文字領域が曖昧",
  crossing_text: "隣接文字と交差", nonhorizontal_text: "縦書き／回転した文字",
  math_font: "数式フォント", short_label_or_cell: "図中ラベル／表セルの可能性",
  overlapping_blocks: "ブロック同士が重なる", invalid_or_large_geometry: "範囲外／長すぎる領域",
  minimum_readable_size: "最小文字サイズで収まらない", glyph_outside_bbox: "文字が領域を超える",
  cached_not_japanese: "保存済み訳に日本語がない",
  translation_failed: "翻訳応答の失敗／不確実", missing_glyph: "フォントにない文字",
  annotations_present: "注釈付き原ページの表示内容を保持",
  ambiguous_caption: "図表とCaptionの対応が曖昧・原ページを保持",
  caption_inside_visual: "Captionを安全に分離できず原ページを保持",
  caption_without_visual: "対応図表が未解決・原ページを保持",
  visual_without_caption: "Captionとの対応未解決・原物を保持",
  table_detection_failed: "表領域を確認できない", text_extraction_mismatch: "文字抽出を確認できない",
};

const TRANSLATION_ERRORS = {
  ai_runtime_error: "AI Runtimeエラー", ai_rpc_error: "AI RPCエラー",
  ai_timeout: "AI timeout", ai_incomplete_turn: "AI turn未完了", ai_cancelled: "AI turn中断",
  invalid_json: "JSON形式不正", schema_mismatch: "schema不一致", block_id_mismatch: "block ID不一致",
  numbers_changed: "数値検証失敗", citations_changed: "引用番号検証失敗",
  references_changed: "図表・数式参照検証失敗", terms_changed: "専門用語検証失敗",
  japanese_required: "日本語未生成", ai_uncertain: "AI uncertain応答",
  translation_validation_error: "その他の翻訳検証エラー", output_too_large: "AI応答サイズ上限",
  translation_attempt_limit: "翻訳回数上限", translation_time_limit: "翻訳時間上限",
};
Object.assign(REASONS, TRANSLATION_ERRORS);
const STAGES = {source_validation:"原文確認",content_detection:"翻訳候補検出",translation:"AI翻訳・応答検証",pdf_layout:"PDF再組版・品質検証",publication:"PDF保存"};

export function createJapanesePdfControls(sourceId, { onSwitch, rememberedId }) {
  let disposed = false, timer, current = null, busy = false, language = "original";
  const original = el("button", { type: "button", class: "btn btn--sm", onClick: () => switchTo("original") }, "原文PDF");
  const japanese = el("button", { type: "button", class: "btn btn--sm", disabled: true, onClick: () => switchTo("japanese") }, "日本語PDF（試作）");
  const download = el("a", { class: "btn btn--sm", hidden: true, download: true }, "日本語PDFを保存");
  const status = el("p", { role: "status", class: "japanese-pdf-status" }, "日本語PDFは未生成です。原文の最大3ページ相当を日本語で再組版します。");
  const diagnostics = el("p", {class:"japanese-pdf-diagnostics", role:"status", hidden:true});
  const pages = el("input", { type: "text", placeholder: "例: 1, 3, 5 または 1-3", "aria-label": "翻訳対象の原文ページ", required: true });
  const submit = el("button", { type: "submit", class: "btn btn--sm" }, "日本語PDFを生成");
  const retry = el("input", { type: "checkbox" });
  const history = el("select", { "aria-label": "生成済み日本語PDF", hidden: true,
    onChange: () => { current = artifacts.find(a => a.generation_id === history.value); update(); if (language === "japanese") switchTo("japanese"); } });
  let artifacts = [];
  const report = el("details", {}, el("summary", {}, "原文との対応・原物保持・未解決の理由"));
  const reportBody = el("div", { class: "japanese-pdf-report" }); report.append(reportBody);
  const form = el("form", { onSubmit: async (event) => {
    event.preventDefault();
    const selected = parsePages(pages.value);
    if (!selected) { toast("1〜3ページを指定してください。例: 1,3,5（重複・範囲外は不可）"); return; }
    busy = true; update();
    try {
      await switchTo("original");
      if (disposed) return;
      const result = await api.generateJapanesePdf(sourceId, selected, retry.checked);
      if (disposed) return;
      current = result; rememberedId = result.generation_id; update(); await refresh();
    } catch (error) { if (!disposed) status.textContent = error.message; }
    finally { busy = false; if (!disposed) updateButtons(); }
  } }, el("label", {}, "原文ページ（最大3） ", pages), submit,
    el("label", {}, retry, " 失敗ブロックを再試行（成功した訳は再利用）"));
  const setup = el("details", {}, el("summary", {}, "日本語版を試作"), form);
  const root = el("div", { class: "japanese-pdf-controls" },
    el("div", { class: "japanese-pdf-actions" }, original, japanese, download, history), status, diagnostics, setup, report);

  async function switchTo(next) {
    if (disposed || (next === "japanese" && current?.state !== "preview_ready")) return;
    await onSwitch(next, current);
    // Reader updates the language synchronously; late loading completions cannot undo it.
  }
  function updateButtons() {
    japanese.disabled = current?.state !== "preview_ready";
    submit.disabled = busy || current?.state === "generating";
    original.setAttribute("aria-pressed", String(language === "original"));
    japanese.setAttribute("aria-pressed", String(language === "japanese"));
  }
  function update() {
    updateButtons();
    download.hidden = current?.state !== "preview_ready";
    if (!download.hidden) download.href = api.japanesePdfFileUrl(sourceId, current.generation_id, true);
    if (current?.state === "generating") status.textContent = "指定ページを翻訳・生成中です。原文PDFは引き続き閲覧できます。";
    else if (current?.state === "preview_ready") {
      const r = current.report;
      status.textContent = `日本語試作・一部ページのみ翻訳済み · ${r.layout_version ? "日本語再組版" : "旧座標版"} · 原文 p.${current.pages.join(", ")} → 日本語 ${r.japanese_page_count || "同数"}ページ · 翻訳対象 ${r.counts.translated || 0}/${r.text_blocks}本文・Caption（${Math.round(r.translated_ratio * 100)}%） · 原物保持 ${(r.counts.skipped_protected || 0)+(r.counts.skipped_unsafe || 0)}領域 · 未翻訳 ${r.counts.failed || 0}ブロック · 未解決原物 ${(r.assets || []).filter(a=>a.status === "unresolved").length}件 · ${r.counts.translated ? "AI翻訳・原文確認推奨" : ""}`;
    } else if (current?.state === "failed") status.textContent = `生成失敗（${current.error}）。原文を閲覧しながら再試行できます。`;
    const d = current?.report?.diagnostics;
    diagnostics.hidden = !d;
    diagnostics.textContent = d ? [
      ...(d.failure_stage ? [`失敗段階：${STAGES[d.failure_stage] || "生成"}`] : []),
      `翻訳候補：${d.translation_candidates}件`, `AI翻訳成功：${d.ai_translation_succeeded}件`,
      `保存済み訳再利用：${d.cached_translations}件`,
      ...Object.entries(d.translation_failures || {}).map(([code,count])=>`${TRANSLATION_ERRORS[code] || "翻訳検証エラー"}（${Object.hasOwn(TRANSLATION_ERRORS,code) ? code : "translation_validation_error"}）：${count}件`),
      `PDF配置成功：${d.pdf_placement_succeeded}件`, `図表・数式保護：${d.protected_units}件`,
      `領域未解決：${d.unsafe_units}件`,
      ...(d.stopped_by ? [`${TRANSLATION_ERRORS[d.stopped_by] || "再試行上限"}で停止`] : []),
    ].join(" · ") : "";
    reportBody.textContent = "";
    if (current?.report) {
      if (d) reportBody.append(el("p", {}, `AI呼出し：${d.runtime_calls || 0}回 · バッチ分割：${d.split_batches || 0}回。再試行中の検出件数（最終失敗数とは別）：` +
        Object.entries(d.attempt_errors || {}).map(([code,count])=>`${TRANSLATION_ERRORS[code] || "翻訳検証エラー"} ${count}件`).join("、")));
      reportBody.append(el("p", {}, `日本語を含む生成ページ: ${current.report.translated_pages?.join(", ") || "なし"}。選択した原文ページ相当のみを収録します。図表・数式と未翻訳の内容は原物を保持します。`));
      for (const asset of current.report.assets || []) {
        reportBody.append(el("p", {}, `${asset.kind} · ${asset.status === "unresolved" ? "未解決・原物保持" : asset.kind === "equation" ? "数式の原物保持" : asset.caption_status === "untranslated" ? "Caption対応済み・未翻訳" : "Caption対応済み"} · 原文p.${asset.source_page} → 日本語p.${asset.japanese_page} · ${REASONS[asset.reason] || (asset.vector_preserved ? "ベクター原物を保持" : "原物の表示内容を保持")}`,
          el("button", { type:"button", class:"btn btn--sm", onClick:()=>onSwitch("original",current,asset.original_block_ids[0]) }, "原文図表を見る")));
      }
      for (const unit of current.report.units) {
        reportBody.append(el("p", {}, `原文 p.${unit.page} → 日本語 p.${unit.placements?.map(p=>p.japanese_page).join(", ") || unit.page} · ${unit.status} · ${REASONS[unit.reason] || unit.reason || "翻訳済み"}`,
          el("button", { type: "button", class: "btn btn--sm", onClick: async () => {
            await switchTo("original"); onSwitch("original", current, unit.block_id);
          } }, "原文を見る")));
      }
    }
  }
  async function refresh() {
    clearTimeout(timer);
    try {
      const result = await api.getJapanesePdf(sourceId);
      if (disposed) return;
      artifacts = result.artifacts || [];
      current = artifacts.find(a => a.generation_id === rememberedId) || artifacts[0] || result;
      const ready = artifacts.filter(a => a.state === "preview_ready");
      history.textContent = "";
      for (const a of ready) history.append(el("option", { value: a.generation_id, selected: a.generation_id === current.generation_id || undefined }, `${a.report?.layout_version ? "再組版" : "旧座標版"} · 原文p.${a.pages.join(", ")}`));
      history.hidden = !ready.length;
      update();
      if (current.state === "generating") timer = setTimeout(refresh, 1500);
    } catch (error) {
      if (!disposed) { status.textContent = error.message; timer = setTimeout(refresh, 5000); }
    }
  }
  const ready = refresh();
  return { root, ready, getArtifact: () => current,
    setLanguage: (next) => { language = next; updateButtons(); },
    dispose: () => { disposed = true; clearTimeout(timer); } };
}

function parsePages(text) {
  const values = [];
  for (const part of text.trim().replaceAll("、", ",").split(",")) {
    const match = part.trim().match(/^(\d+)(?:\s*-\s*(\d+))?$/);
    if (!match) return null;
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    if (start < 1 || end < start || end - start > 2) return null;
    for (let n = start; n <= end; n++) values.push(n);
  }
  return values.length >= 1 && values.length <= 3 && new Set(values).size === values.length ? values : null;
}
