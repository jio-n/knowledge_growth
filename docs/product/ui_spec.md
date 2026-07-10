# UI 仕様(読解体験の正本)

最終更新: 2026-07-11 / 状態: 確定(v1)。実装: `client/`。API契約は api_spec.md、アンカーは source_anchor_spec.md。

## 技術規約

- ビルド不要 Vanilla JS(ES Modules)。フレームワーク禁止(Node非依存のため)。
- vendored ライブラリ: `client/vendor/pdf.mjs`, `pdf.worker.mjs`(pdf.js), `marked.esm.js`(Markdown表示)。CDN参照禁止(ローカルファースト)。
- 状態はサーバーが正本。クライアントは表示状態のみ保持(例外: 資料ごとのスクロール位置・ペイン幅・タブ選択は localStorage `kg.reader.<sourceId>`)。
- CSS はプレーン1ファイル `client/css/app.css`。CSS変数でテーマ定義(ライト基調)。
- すべてのAI由来テキストは `marked.parse()` で表示し、コンテナに `.ai-content` クラス。
- XSS: marked出力は資料本文には使わない。資料ブロックは textContent で挿入(innerHTML禁止)。

## 画面1: ライブラリ(`#/`)

```
┌──────────────────────────────────────────────┐
│ ◆ Research Workspace   [横断検索________] [meta: provider表示] │
│ [+ PDF] [+ URL] [+ テキスト]   フィルタ: [状態▼] [タグ▼]     │
├──────────────────────────────────────────────┤
│ ▤ 資料カード(タイトル/著者/年/種別バッジ/一言要約/          │
│    タグ/読書状態セレクタ/知識n・質問n・未解決n/最終閲覧)     │
│    クリック → 読解画面(#/read/<id>)                          │
└──────────────────────────────────────────────┘
```

- 追加ボタン → モーダル(addsource.js)。URL/テキストはフォーム、PDFは file input。
- 登録応答が `duplicate:true` → 「既存を開く」「新規として登録」の2択ダイアログ。
- 登録成功 → 即読解画面へ遷移。analysis_status が pending/running の間、概要タブに進行表示(3秒ポーリング)。
- 横断検索: 入力→ GET /api/search。結果ドロップダウン(種別バッジ+資料名+snippet)。クリックで該当資料の読解画面へ(knowledge/questionはノート/対話タブを開き該当項目へスクロール&フラッシュ)。

## 画面2: 読解画面(`#/read/<id>`)

```
┌─ ヘッダー: [← ライブラリ] タイトル | 状態セレクタ | [前の資料][次の資料] | [MD出力] ─┐
├───────────────────────────┬──────────────────────┤
│  原文ペイン(左, リサイズ可)         │  右ペイン: [概要][対話][ノート]  │
│  - PDF: pdf.jsページ描画+テキストレイヤー │                              │
│    ↔ 「テキスト表示」トグルでブロック表示へ │                              │
│  - web/text/md: ブロック表示            │                              │
│  - ブロックhoverで [訳] ボタン           │                              │
│  - テキスト選択 → ポップオーバー          │                              │
└───────────────────────────┴──────────────────────┘
```

ペイン幅はドラッグで変更、localStorageに保存。前/次の資料 = ライブラリ一覧順で隣へ(F-15)。

### 原文ペイン

- **ブロック表示**(web/text/markdown、PDFの代替表示): document.blocks を kind に応じてレンダリング(heading→h2-h4, para→p, code→pre, quote→blockquote, list→li風, table/figure→枠付きp)。各要素に `data-block-id`, `data-block-idx`, `data-page` を付与。翻訳が存在するブロックは原文直下に `.translation` div(青左ボーダー)で訳文表示+「訳を隠す」。
- **PDF表示**: pdf.js で全ページを順次 canvas 描画 + TextLayer(選択可能)。ページ区切りにページ番号。上部トグル「PDF表示 / テキスト表示」(既定PDF)。PDF表示でもテキスト選択→ポップオーバーが機能(アンカーは page + quote、blockIdx はページ先頭ブロックで近似)。
- **文書内検索**: ヘッダーの検索欄。ブロック表示=一致ブロックへ順次ジャンプ+ハイライト。PDF表示=抽出テキストで一致ページへジャンプ。
- **ハイライト**: document.highlights をブロック表示で該当テキストに `<mark>` 適用(quote検索ベース)。クリックで削除メニュー。

### 選択ポップオーバー(selection.js)

テキスト選択のmouseupで選択範囲近くに表示:
`[質問する] [説明] [初学者向け] [詳しく] [批判的に] [応用を考える] [翻訳] [ハイライト] [原文を保存]`

- 説明/初学者向け/詳しく/批判的に/応用 → 即 POST /questions(prompt_type、question_text=""、anchor付き)→ 対話タブへ切替、ローディング表示→回答表示。
- 質問する → 対話タブへ切替、選択箇所を引用チップとして質問入力欄へセット(自由文入力後送信、prompt_type=free)。
- 翻訳 → POST /api/translate。選択がブロック全体に近い場合(選択文字数 ≥ ブロックの80%)は block_id 付き(永続化)、それ以外は臨時翻訳としてポップオーバー下にカード表示+「用語として保存」ボタン。
- ハイライト → POST /api/highlights(anchor付き)、即 `<mark>` 反映。
- 原文を保存 → 保存ダイアログ(origin=source_quote, info_type=fact 初期値)。

### 右ペイン: 概要タブ(summary.js)

- analysis_status=pending/running: スピナー+「構造化抽出を実行中」(3秒ポーリング)。
- done: origin=auto_extract の知識項目をセクション順に表示。各項目に「AI自動抽出」バッジ。error: エラー表示+[再実行]。
- [再解析] ボタン(確認ダイアログ: 自動抽出のみ置換されユーザー保存分は残る旨明記)。
- 書誌メタデータ(著者/年/DOI/arXiv/URL/取得日時)と原文リンク。

### 右ペイン: 対話タブ(qa.js)

- 質問カードの時系列リスト。各カード: 引用チップ(selection_text冒頭+📍位置 — クリックで原文へジャンプ=アンカー解決)、質問文、回答(marked表示、`AI回答 / <model>` バッジ)。
- 回答カードのアクション: [全体を保存] [選択部分を保存](回答内テキスト選択時のみ活性) [この回答にさらに質問]。
- 保存済み回答には「保存済→ノート」チップ。
- 下部に自由質問入力欄(選択なしの資料全体質問、prompt_type=free)。送信中はスピナー+入力ロック。
- LLMエラー(502)はカード内にエラー表示+[再試行]。

### 保存ダイアログ(savedialog.js)

開いた瞬間に POST /api/knowledge/suggest を呼び、返答で保存先セクション・情報種別・タイトルをプリセレクト(体感遅延を避けるためダイアログは先に表示、提案が来たら反映)。
```
保存する内容(編集可 textarea、選択部分 or 全体がプリフィル)
タイトル(任意、提案値)
保存先セクション: [セレクト(note_template)]  情報種別: [セレクト]
[キャンセル] [保存]   ← 保存 = 2クリック目標(開く→保存)
```
保存後トースト「ノートに保存しました [表示]」。

### 右ペイン: ノートタブ(note.js)

- 一言要約(編集可 → PATCH source ... ではなく knowledge? → one_line_summaryはsources列: PATCH /api/sources)。
- note_template セクション順に知識項目カード表示。カード構成: origin バッジ(色分け: 原文=緑/AI=紫/AI編集済=紫枠/ユーザー=青/自動抽出=灰)+ info_type ラベル + 内容(markdown)+ 📍原文リンク + 由来Q&Aリンク(対話タブの該当カードへ)。
- カード操作(hover): [編集(textarea化)] [セクション移動▼] [検証状態切替(未検証→確認済→要再確認)] [削除]。
- 各セクション末尾 [+ メモを追加](origin=user, info_type=user_thought)。
- ヘッダー [Markdownで見る] → モーダルで export.md プレビュー(pre表示)+[ダウンロード] [JSONダウンロード]。

## バッジ色の意味(§13) — app.css で CSS変数化

| origin | 色 | ラベル |
|--------|----|--------|
| source_quote | 緑 | 原文 |
| auto_extract | 灰 | AI自動抽出 |
| llm | 紫 | AI回答 |
| llm_edited | 紫(枠線) | AI回答·編集済 |
| user | 青 | 自分 |

info_type は小さいテキストラベル(事実/著者の主張/実験結果/AI要約/AI解釈/考察/未解決/アイデア/用語/アクション/翻訳)。

## アンカー解決(resolveAnchor — source_anchor_spec.md のアルゴリズム実装)

ノート/対話の📍クリック → 原文ペインで解決 → 対象ブロック(またはPDFページ)へ smooth scroll + 2秒間の黄色フラッシュ。PDF表示中に blockId 解決が必要な場合は page ジャンプ+テキストレイヤー内 quote 検索を試みる。失敗時トースト。

## ファイル構成

```
client/index.html            SPAシェル(ヘッダー+#app)
client/css/app.css
client/js/main.js            ハッシュルーター(#/ と #/read/<id>)、起動時 /api/meta
client/js/api.js             fetchラッパー(エラートースト共通処理)
client/js/state.js           現在資料・meta・ライブラリキャッシュ
client/js/util.js            el()ヘルパー、toast、debounce、escapeHtml
client/js/views/library.js
client/js/views/reader.js    ペイン骨格、タブ、アンカー解決、資料前後ナビ
client/js/components/docviewer.js   ブロック表示+翻訳表示+ハイライト適用
client/js/components/pdfviewer.js   pdf.js描画+テキストレイヤー+トグル
client/js/components/selection.js   ポップオーバー+makeAnchor
client/js/components/qa.js
client/js/components/summary.js
client/js/components/note.js
client/js/components/savedialog.js
client/js/components/addsource.js
```
