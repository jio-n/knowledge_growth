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
  table_detection_failed: "表領域を確認できない", text_extraction_mismatch: "文字抽出を確認できない",
};

export function createJapanesePdfControls(sourceId, { onSwitch, rememberedId }) {
  let disposed = false, timer, current = null, busy = false, language = "original";
  const original = el("button", { type: "button", class: "btn btn--sm", onClick: () => switchTo("original") }, "原文PDF");
  const japanese = el("button", { type: "button", class: "btn btn--sm", disabled: true, onClick: () => switchTo("japanese") }, "日本語PDF（試作）");
  const download = el("a", { class: "btn btn--sm", hidden: true, download: true }, "日本語PDFを保存");
  const status = el("p", { role: "status", class: "japanese-pdf-status" }, "日本語PDFは未生成です。最大3ページを試作できます。");
  const pages = el("input", { type: "text", placeholder: "例: 1, 3, 5 または 1-3", "aria-label": "翻訳対象ページ", required: true });
  const submit = el("button", { type: "submit", class: "btn btn--sm" }, "日本語PDFを生成");
  const retry = el("input", { type: "checkbox" });
  const history = el("select", { "aria-label": "生成済み日本語PDF", hidden: true,
    onChange: () => { current = artifacts.find(a => a.generation_id === history.value); update(); if (language === "japanese") switchTo("japanese"); } });
  let artifacts = [];
  const report = el("details", {}, el("summary", {}, "翻訳済みページ・未翻訳領域と理由"));
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
  } }, el("label", {}, "対象ページ（最大3） ", pages), submit,
    el("label", {}, retry, " 失敗ブロックを再試行（成功した訳は再利用）"));
  const setup = el("details", {}, el("summary", {}, "日本語版を試作"), form);
  const root = el("div", { class: "japanese-pdf-controls" },
    el("div", { class: "japanese-pdf-actions" }, original, japanese, download, history), status, setup, report);

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
      status.textContent = `日本語試作・一部ページのみ翻訳済み · 対象 p.${current.pages.join(", ")} · 置換 ${r.counts.translated || 0}/${r.text_blocks}テキストブロック（${Math.round(r.translated_ratio * 100)}%） · ${r.counts.translated ? "AI翻訳・原文確認推奨" : ""}`;
    } else if (current?.state === "failed") status.textContent = `生成失敗（${current.error}）。原文を閲覧しながら再試行できます。`;
    reportBody.textContent = "";
    if (current?.report) {
      reportBody.append(el("p", {}, `日本語を含むページ: ${current.report.translated_pages?.join(", ") || "なし"}。対象外ページと未置換領域は英語原文のままです。`));
      for (const unit of current.report.units) {
        reportBody.append(el("p", {}, `p.${unit.page} · ${unit.status} · ${REASONS[unit.reason] || unit.reason || "置換済み"}`,
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
      for (const a of ready) history.append(el("option", { value: a.generation_id, selected: a.generation_id === current.generation_id || undefined }, `p.${a.pages.join(", ")}`));
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
