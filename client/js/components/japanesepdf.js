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
  metadata_preserved:"本文と分けて原文情報を保持",title_assembly_ambiguous:"タイトル構造を確認できず全文を原文で保持",
  decorative_rule:"周辺の飾り罫線・本文外として対応を保持",
  block_too_long:"翻訳単位の入力上限・原文を文章として保持",
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
  assembled_user_edit_review:"構成blockにユーザー編集あり・全文確認が必要",
  title_incomplete:"タイトルの意味要素が不足",title_review_invalid:"タイトル完全性の応答不正",
  title_review_uncertain:"タイトルの意味対応が不確実",title_review_limit:"タイトル完全性の検証回数上限",
  urls_changed:"URL検証失敗",inline_math_changed:"本文内数式の検証失敗",
};
Object.assign(REASONS, TRANSLATION_ERRORS);
const STAGES = {source_validation:"原文確認",content_detection:"翻訳候補検出",translation:"AI翻訳・応答検証",pdf_layout:"PDF再組版・品質検証",publication:"PDF保存"};
const VISUAL_KINDS = {original_text:"未翻訳本文",figure:"Figure",table:"Table",equation:"Equation",annotated_page:"注釈付き原ページ"};
const VISUAL_CLASSES = {raster_alignment:"描画位相差を補正・内容一致",native_content_preserved:"原物の内容一致",
  clip_geometry:"clip／寸法不一致",unexpected_text:"不要な文字の混入",text_missing_or_changed:"文字の欠落・変更",text_order_changed:"文字順序の不一致",
  glyph_geometry_changed:"文字配置・描画属性不一致",
  font_resource_changed:"フォント資源不一致",drawing_or_image_changed:"線・図形・画像不一致",
  drawing_changed:"線・図形不一致",image_changed:"画像不一致",drawing_and_image_changed:"線・図形と画像不一致",
  nonvisible_drawing_commands:"非表示の描画命令差・可視内容完全一致",
  annotation_appearance_changed:"注釈の表示不一致",rendered_content_changed:"表示内容不一致",
  validation_limit:"検査上限",validation_unavailable:"検査不能",crop_unavailable:"原物の切り出し不能"};

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
      const percent=(value)=>value == null ? "対象なし" : `${Math.round(value*100)}%`;
      const c=r.coverage;
      const coverage=c ? `本文の日本語カバレッジ ${percent(c.body.ratio)}（原文文字数 ${c.body.translated_source_chars}/${c.body.source_chars}） · 要旨 ${percent(c.abstract.ratio)} · タイトル ${!c.title.units ? "対象なし" : c.title.translated && c.title.complete ? "全文を日本語化" : "全文を原文保持・確認対象"}` : `旧指標：翻訳block件数 ${r.counts.translated || 0}/${r.text_blocks}（${Math.round(r.translated_ratio*100)}%・本文カバレッジとは異なります）`;
      status.textContent = `日本語試作・一部ページのみ翻訳済み · ${r.layout_version ? "日本語再組版" : "旧座標版"} · 原文 p.${current.pages.join(", ")} → 日本語 ${r.japanese_page_count || "同数"}ページ · ${coverage} · 未翻訳 ${r.counts.failed || 0}意味単位 · 未解決原物 ${(r.assets || []).filter(a=>a.status === "unresolved").length}件 · AI翻訳・原文確認推奨`;
      if (c?.abstract.source_chars && !c.abstract_complete) status.textContent += " · PDF生成済み／要旨の日本語化は未完了";
      if (r.visual_quality_version !== "native-3") status.textContent += " · 新しい原物検証は未実施（再生成できます）";
      if (r.assembly_version !== "semantic-1") status.textContent += " · 文章構造の再構成は未実施（再生成できます）";
      if (r.overlap_version !== "glyph-ink-2") status.textContent += " · 文字重なり検証は旧方式（再生成できます）";
      if (r.extraction_version !== "glyph-spacing-1") status.textContent += " · 本文の抽出・空白復元は旧方式（再生成できます）";
    } else if (current?.state === "failed") status.textContent = `生成失敗（${current.error}）。原文を閲覧しながら再試行できます。`;
    const d = current?.report?.diagnostics;
    diagnostics.hidden = !d;
    diagnostics.textContent = d ? [
      ...(d.failure_stage ? [`失敗段階：${STAGES[d.failure_stage] || "生成"}`] : []),
      `翻訳候補：${d.translation_candidates}件`, `AI翻訳成功：${d.ai_translation_succeeded}件`,
      `保存済み訳再利用：${d.cached_translations}件`,
      ...Object.entries(d.translation_failures || {}).map(([code,count])=>`${TRANSLATION_ERRORS[code] || "翻訳検証エラー"}（${Object.hasOwn(TRANSLATION_ERRORS,code) ? code : "translation_validation_error"}）：${count}件`),
      `PDF配置成功：${d.pdf_placement_succeeded}件`,
      ...(d.pdf_placement_validated != null ? [`配置検証済み：${d.pdf_placement_validated}件`, `PDF公開：${d.publication_state === "published" ? "完了" : d.publication_state === "failed" ? "失敗" : "未完了"}`] : []), `図表・数式保護：${d.protected_units}件`,
      `領域未解決：${d.unsafe_units}件`,
      ...Object.entries(d.visual_failures || {}).map(([kind,count])=>`原物検証失敗（${VISUAL_KINDS[kind] || "原物"}）：${count}件`),
      ...(d.stopped_by ? [`${TRANSLATION_ERRORS[d.stopped_by] || "再試行上限"}で停止`] : []),
    ].join(" · ") : "";
    reportBody.textContent = "";
    if (current?.report) {
      if (d) reportBody.append(el("p", {}, `AI呼出し：${d.runtime_calls || 0}回 · バッチ分割：${d.split_batches || 0}回。再試行中の検出件数（最終失敗数とは別）：` +
        Object.entries(d.attempt_errors || {}).map(([code,count])=>`${TRANSLATION_ERRORS[code] || "翻訳検証エラー"} ${count}件`).join("、")));
      if (current.report.coverage) reportBody.append(el("p", {},
        `本文の読み順を意味単位で構成。本文として識別した原物保持：${current.report.coverage.body_preserved_in_visuals}件（境界確認） · 周辺情報：${d?.metadata_units || 0}単位 · 元blockの対応重複：${current.report.quality?.source_mapping_duplicates ?? "未検査"}`));
      const contacts={bbox_separated:"矩形は非交差",bbox_only:"矩形のみ接触・inkなし",ink_contact:"ink接触",contact_unproven:"局所検証不能"};
      const seedKinds={drawing:"描画",image:"画像",detected_table:"検出Table",numeric_table:"数値列Table",equation:"Equation",isolated_math_or_special_font:"独立した数式・特殊font",text_collision:"文字衝突・未証明"};
      for (const page of current.report.content_detection || []) {
        reportBody.append(el("p",{class:"japanese-pdf-overlap-quality"},
          `文字重なり検証 · 原文p.${page.source_page} · ` + Object.entries(page.pair_counts || {}).map(([kind,count])=>`${kind} ${count}組`).join("、") +
          ` · 境界未解決 ${page.boundary_conflicts?.length || 0}件 · 比較上限 ${page.pair_limit_reached ? "到達" : "未到達"}`));
        for (const seed of page.seeds || []) reportBody.append(el("p",{class:"japanese-pdf-seed-quality"},
          `seed ${seed.id} · ${seedKinds[seed.kind] || "原物"} · object ${seed.object_index} · 保持必須領域 ${(seed.core_bbox_pt || []).join(", ")}pt`));
        for (const boundary of page.boundary_checks || []) reportBody.append(el("p",{class:"japanese-pdf-boundary-quality"},
          `${boundary.group_id} · seed ${(boundary.seed_ids || []).join(", ")} · 元block ${boundary.block_id} · ` +
          `${contacts[boundary.before?.classification] || "未検証"} → ${contacts[boundary.minimum_crop?.classification] || "未検証"} · `+
          `${boundary.resolution === "padding_trimmed" ? "追加余白を縮小・分離済み" : "境界未解決"} · `+
          `原物と全文字範囲 ${boundary.mandatory_seed_and_font_boxes_preserved ? "保持" : "未確認"}` +
          (boundary.minimum_crop?.proof_error ? ` · ${boundary.minimum_crop.proof_error}` : "")));
        for (const pair of page.pairs || []) reportBody.append(el("p",{},
          `${pair.classification} · baseline差 ${pair.minimum_baseline_difference_pt ?? "未確認"}pt · glyph数 ${(pair.glyph_counts || []).join(" / ")} · 交差ink ${pair.overlapping_ink_pixels ?? "未確認"}px · 原描画照合 ${pair.source_render_verified ? "一致" : "未確認"}`));
        for (const region of page.regions || []) reportBody.append(el("p",{},
          `保護領域 ${region.member_count}block · 種領域 ${(region.seed_size_pt || []).join("×")}pt → crop ${(region.crop_size_pt || []).join("×")}pt · 拡張 ${region.expansion_steps}回`));
      }
      for (const page of current.report.content_detection || []) for (const recovery of page.prose_recovery || [])
        reportBody.append(el("p",{class:"japanese-pdf-prose-recovery"},`通常本文の文字・輪郭を検証して再組版 · 原文p.${page.source_page} · ${recovery.source_chars}文字（隣接組ごとの計数） · glyph数 ${recovery.glyph_counts.join(" / ")} · 科学的原物と非交差`));
      const categories={research_subject:"研究対象",application_domain:"適用領域",method_model:"手法・モデル",research_positioning:"研究の位置付け",identifiers:"固有名詞・識別子"};
      for (const group of current.report.semantic_units || []) if (group.title_review) {
        const review=group.title_review;
        reportBody.append(el("p",{class:"japanese-pdf-title-quality"},`タイトル意味検証 ${review.status} · 未対応語数 ${review.unmapped_source_tokens ?? "未再計測"} · ${review.reused ? "検証済み訳を再利用" : ""}`));
        for (const check of review.checks || []) reportBody.append(el("p",{},`${categories[check.category] || "意味要素"}：${check.status} · 原文 ${check.source_chars}文字／訳文 ${check.translated_chars}文字`));
      }
      const a=current.report.coverage?.abstract;
      if (a && a.candidate_source_chars != null) reportBody.append(el("p",{class:"japanese-pdf-abstract-quality"},
        `要旨本文（原文文字数）：原文 ${a.source_chars} · 翻訳候補 ${a.candidate_source_chars} · 日本語化 ${a.translated_source_chars} · 原物保護 ${a.protected_source_chars} · PDF配置 ${a.placed_source_chars}（日本語 ${a.placed_translated_source_chars}） · `+
        `未翻訳理由：${Object.entries(a.untranslated_reasons || {}).map(([code,count])=>`${REASONS[code] || "未翻訳"} ${count}文字`).join("、") || "なし"}`));
      const semanticRoles={paper_title:"論文タイトル",abstract_heading:"要旨見出し",abstract_body:"要旨本文",paragraph:"段落",section_heading:"Section見出し",caption:"Caption",authors:"著者",affiliations:"所属",contact:"連絡先",bibliographic:"書誌情報",marginal_metadata:"周辺識別情報",running_header:"Running header",footer:"原文footer",footnote:"原文脚注",page_decoration:"飾り罫線",scientific_visual:"図表・数式"};
      const stageText=(group)=> group.ai_translation_state ?
        `AI翻訳 ${group.ai_translation_state === "succeeded" ? "成功" : group.ai_translation_state === "failed" ? "未成功" : "対象外／未完了"}` +
        (group.ai_translation_error ? `（${TRANSLATION_ERRORS[group.ai_translation_error] || "翻訳検証エラー"}）` : "") +
        ` · 配置 ${group.placement_state} · 原物検証 ${group.visual_quality_state} · PDF公開 ${group.publication_state}` :
        `${group.status} · ${REASONS[group.reason] || group.reason || "翻訳済み"}`;
      for (const group of current.report.semantic_units || []) reportBody.append(el("p",{class:"japanese-pdf-semantic-unit"},
        `${semanticRoles[group.role] || "意味単位"} · 構成block ${group.original_block_ids.length}件 · 原文${group.source_chars ?? "?"}文字 · ${stageText(group)}`+
        (group.extraction ? ` · 空白復元 ${group.extraction.inferred_spaces}箇所 · 原文字照合 ${group.extraction.glyph_text_matched ? "一致" : "要確認"}` : "")+
        (group.protection_reasons?.length ? ` · 保護判定 ${group.protection_reasons.join(", ")}` : ""),
        el("button",{type:"button",class:"btn btn--sm",onClick:()=>onSwitch("original",current,group.original_block_ids[0])},"原文の意味単位を見る")));
      for (const q of current.report.quality?.visuals || []) {
        reportBody.append(el("p", {class:"japanese-pdf-visual-quality"},
          `${VISUAL_KINDS[q.annotations_present ? "annotated_page" : q.asset_kind] || "原物"} · ${q.asset_id} · 原文p.${q.source_page ?? "?"} · ${q.status === "failed" ? "検証失敗" : "内容照合済み"} · ${VISUAL_CLASSES[q.classification] || "原物検証"} · ` +
          `原物寸法 ${(q.source_size_pt || []).join("×")}pt · 比較 ${(q.source_image_size_px || []).join("×")} / ${(q.output_image_size_px || []).join("×")}px · ` +
          `平均pixel差 ${q.mean_pixel_difference ?? "未測定"}（補正前 ${q.raw_mean_pixel_difference ?? "未測定"}） · 文字一致 ${q.source_text_preserved ? "はい" : "未確認／不一致"} · ${q.vector_preserved === false ? "画像化（200dpi）" : q.vector_preserved === true ? "ベクター保持" : "未配置／未確認"}`));
        const check = (value) => value === true ? "一致" : value === false ? "不一致" : "未検査";
        reportBody.append(el("p", {class:"japanese-pdf-visual-structure"},
          `描画命令：${check(q.drawings_preserved)} · 画像：${check(q.images_preserved)} · glyph：${check(q.glyphs_preserved)} · font：${check(q.fonts_preserved)}` +
          (q.quality_policy === "original_text" ? ` · 可視clip内描画：${check(q.visible_drawings_preserved)} · 非表示命令数 原文${q.source_invisible_drawing_count ?? "?"}／日本語${q.output_invisible_drawing_count ?? "?"}` : " · 図表・数式の完全構造照合") +
          (q.nonvisible_drawing_difference_proven ? " · clip外の命令差を証明済み（pixel完全一致）" : "")));
      }
      for (const q of current.report.quality?.visuals || []) if (q.crop_text_diagnostics) {
        const t=q.crop_text_diagnostics;
        reportBody.append(el("p",{class:"japanese-pdf-crop-text"},
          `原物文字検査 · ${q.asset_id} · 元block ${q.original_block_chars ?? "?"}文字 · 原文clip ${t.source_clip_chars ?? "?"}文字 · 境界をまたぐglyph ${t.boundary_straddling_glyphs ?? "?"}件 · `+
          `外側削除後の欠落 ${t.after_outside_redaction?.missing_chars ?? "?"}文字 · crop欠落 ${t.native_crop?.missing_chars ?? "?"}文字／混入 ${t.native_crop_all_text?.extra_chars ?? "?"}文字 · `+
          `crop完全性 ${q.crop_text_complete ? "一致" : "不一致"} · crop全文字照合 ${q.crop_no_hidden_text ? "一致" : "不一致"} · glyphコード ${q.glyph_codes_preserved ? "一致" : "不一致"} · 文字差の発生段階 ${q.text_failure_stage || "なし"}`));
      }
      reportBody.append(el("p", {}, `日本語を含む生成ページ: ${current.report.translated_pages?.join(", ") || "なし"}。選択した原文ページ相当のみを収録します。図表・数式は原物を保持し、確認できた未翻訳本文は英文として再組版します。`));
      for (const asset of current.report.assets || []) {
        reportBody.append(el("p", {}, `${asset.kind} · ${asset.status === "unresolved" ? "未解決・原物保持" : asset.kind === "equation" ? "数式の原物保持" : asset.caption_status === "untranslated" ? "Caption対応済み・未翻訳" : "Caption対応済み"} · 原文p.${asset.source_page} → 日本語p.${asset.japanese_page} · ${REASONS[asset.reason] || (asset.vector_preserved ? "ベクター原物を保持" : "原物の表示内容を保持")} · ${asset.vector_preserved === false ? "画像化（200dpi・内容照合済み）" : "ベクター保持"}`,
          el("button", { type:"button", class:"btn btn--sm", onClick:()=>onSwitch("original",current,asset.original_block_ids[0]) }, "原文図表を見る")));
      }
      for (const unit of current.report.units) {
        reportBody.append(el("p", {}, `原文 p.${unit.page} → 日本語 p.${unit.placements?.map(p=>p.japanese_page).join(", ") || unit.page} · ${stageText(unit)}`,
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
      for (const a of ready) history.append(el("option", { value: a.generation_id, selected: a.generation_id === current.generation_id || undefined }, `${a.report?.layout_version === "reflow-6" ? "本文再組版改善版（境界分離検証版）" : a.report?.overlap_version === "glyph-ink-2" ? "境界分離検証版（文字重なり検証版）" : a.report?.overlap_version === "glyph-ink-1" ? "旧文字重なり検証版" : a.report?.extraction_version === "glyph-spacing-1" ? "本文抽出改善版" : a.report?.assembly_version === "semantic-1" ? "旧文章構造版" : a.report?.layout_version ? "旧再組版" : "旧座標版"} · 原文p.${a.pages.join(", ")}`));
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
