# knowledge_growth MVP仕様 v0.4

最終更新: 2026-10-04
状態: 再設計案 / 実装前仕様
対象: knowledge_growth Research Reading Workspace

> **v0.4の優先決定**: 通常利用でAPI従量課金を使わない。第一候補AI runtimeは **Codex app-server + ChatGPTプラン**。また、ChatGPTで論文を解析した成果を `.kgpack` で取り込む **ChatGPT Import Bridge** をMVPに追加する。以下の「v0.4追加・上書き仕様」は、後段のv0.3本文と矛盾する場合に優先する。

---

## v0.4-A. AI利用の基本方針 — Subscription-first

knowledge_growth の通常利用では、**OpenAI APIキーや他社APIキーを使う従量課金AIを前提にしない**。

AI利用の優先順位:

1. **Codex app-server + ChatGPTプラン** — Paper Brief、翻訳、AI質問、会話等の第一候補
2. **ChatGPT Import Bridge** — このChatGPT等で論文を解析し、構造化成果物をローカルアプリへ取り込む
3. **mock / no-AI mode** — AI未接続でも原PDF・ノート・Import済み情報を閲覧可能
4. **従量課金API provider** — 将来拡張点として抽象化は保つが、MVPでは実装・設定・利用を要求しない

### 非前提・禁止

- MVP起動時に `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等を要求しない。
- API従量課金providerをデフォルトにしない。
- APIキーを設定ファイル・SQLite・Gitへ保存しない。
- AI接続不能や利用上限到達で、原PDF・ノート・Import済み成果物を閲覧不能にしない。
- 従量課金APIを使わないとMVP受入条件を満たせない設計にしない。

### AI Runtime Adapter

UI・Paper Brief・翻訳・Q&Aは、特定AI接続方式へ直接依存させず `AIRuntime` adapterを介する。

```text
AIRuntime
├ codex_chatgpt_plan    ← MVP第一候補
├ mock / no-ai          ← 開発・フォールバック
└ api_provider          ← 将来拡張。MVP非対象
```

Codex app-server / Sign in with ChatGPTがプレビュー仕様であるため、ResearchSource、Evidence、Note、TranslationUnit等の保存形式をCodex固有形式にしない。

---

## v0.4-B. ChatGPT Import Bridge（MVP追加）

### 目的

ChatGPT上で論文をアップロードしてPaper Brief・Evidence・メモ候補等を生成し、**API従量課金を使わずに**knowledge_growthへ取り込む。

```text
ChatGPT
  ↓ 論文解析
Paper Brief / Evidence / Knowledge候補 / Q&A候補
  ↓
*.kgpack
  ↓
knowledge_growth [ChatGPTから取り込む]
  ↓
Import Preview
  ↓
ローカルPDFと照合・Anchor再解決
  ↓
ユーザー確認後にSQLiteへ反映
```

### `.kgpack`

ZIP互換コンテナ。**原則として論文PDF原本を含めない**。

```text
example.kgpack
├ manifest.json
├ paper_brief.json
├ evidence_refs.json
├ knowledge_items.json      # 任意
├ qa_threads.json           # 任意
└ assets/                   # 任意。再配布可能な素材のみ
```

`manifest.json`:
- package_schema_version
- package_id
- source_identity: title / DOI / arXiv ID / optional source hash
- generated_by / generated_at
- paper_brief_schema_version
- provenance_notice

### ローカルPDFとの照合

1. source hash
2. DOI / arXiv ID
3. title + authors + year
4. ユーザー指定

一致しない場合は自動適用しない。

### Evidence再解決

外部成果物では `page + quote + heading + figure/table label` 等の可搬参照を保存する。Import時にローカルのdocument_blocks / bbox / SourceAnchorへ再解決する。

- 成功: local block_id / bboxを付与
- 候補複数: Import Previewで確認
- 失敗: `unresolved` として保持し、確定根拠扱いしない

### Import Preview

反映前に必ず表示:
- 対象論文
- 追加・更新されるPaper Brief field
- Knowledge Item / Q&A候補
- Evidence解決率 / unresolved件数
- 既存ユーザー編集との競合

`origin=user` や user-edited項目は自動上書きしない。

### Provenance

Import由来項目は `origin=chatgpt_import` 等で区別し、package_id、imported_at、schema_version、generator label、evidence statusを保持する。ImportしたAI生成物を原文事実として表示しない。

### 将来

MCP等でChatGPT/Codexから直接書き込む方式は将来候補。MVPは `.kgpack` の明示的Importを正本とする。

---

## v0.4-C. MVP追加受入条件

| ID | 完了条件 |
|---|---|
| M-AI-01 | MVPの通常利用に従量課金APIキーを要求しない |
| M-AI-02 | Codex app-server + ChatGPTプランを第一候補AI runtimeとして利用できる |
| M-AI-03 | AI接続不能でも原PDF・ノート・Import済み成果を利用できる |
| M-IMP-01 | `.kgpack` をImport Preview経由で既存ローカル論文へ取り込める |
| M-IMP-02 | Import PackageにPDF原本を含めなくても反映できる |
| M-IMP-03 | Import EvidenceをローカルAnchorへ再解決し、未解決を明示できる |
| M-IMP-04 | Import由来AI項目にprovenanceが残る |
| M-IMP-05 | 既存ユーザー編集を自動上書きしない |

---

# v0.3本文（v0.4で未変更の仕様）

## 0. プロダクト定義

knowledge_growth は、単なるPDFビューア・翻訳ツール・論文チャット・メモ帳ではない。

> **原論文の構造・図表・根拠を保ったまま、日本語で読み、原文と往復しながらAIに質問し、自分の理解をテキスト・画像・根拠位置付きで蓄積する論文Reader**

MVPでは「読む → 理解する → 残す → 根拠へ戻る」を最短距離で成立させる。

---

# 1. MVPの全体フロー

```text
PDF登録
  ↓
Paper Brief（この論文は何を言っている？）
  ↓
日本語版 / 原文PDF / 比較 で読む
  ↓
気になる箇所
  ├ AIに質問
  ├ 会話を続ける
  ├ ハイライト
  ├ テキストメモ
  └ Visual Clip（図・表・領域を画像付きで保存）
  ↓
必要な内容だけ理解ノートに採用
  ↓
📍 根拠へ戻る
  ↓
Paper Map / 論文内検索 / Markdown・JSON出力
```

---

# 2. 読み込み直後: Paper Brief

## 2.1 目的

論文本文を読み始める前に、30秒〜2分程度で「この論文は何をした論文か」をつかむ。

単純な長文要約ではなく、**構造化スキーマ**で表示する。

Paper BriefはAI生成物であり、原文事実と混同しない。各項目には可能な限り根拠となる原文位置・Figure/Tableを保持する。

---

## 2.2 2層構造

### Layer A: 30秒Brief

画面上部に、最重要項目だけを短く表示する。

1. **一言要約**
2. **研究目的**
3. **何が新しいか**
4. **対象タスク / 対象ドメイン**
5. **主な結果**
6. **この論文を読む理由 / 注目箇所**

例:

```text
AnomalyGPT

一言で:
LVLMを産業異常検知へ適用し、異常の有無だけでなく位置と自然言語説明まで行う手法。

目的:
一般VLMが苦手な産業画像の局所異常を、少数正常画像条件で扱う。

新規性:
LVLM + Prompt Learner + Image Decoder を統合。

主な結果:
MVTec-ADで image-level AUC xx.x / pixel-level AUC xx.x（原論文確認必須）
```

### Layer B: Structured Brief

詳細は折りたたみ可能な構造化項目として表示する。

---

# 3. Paper Brief スキーマ

## 3.1 全論文共通 Core Schema

| フィールド | 内容 |
|---|---|
| paper_type | 手法提案 / benchmark / survey / dataset / analysis / system / position 等 |
| one_line_summary | 一言で何をした論文か |
| research_objective | 研究目的 |
| background | 背景 |
| problem | 解こうとしている課題 |
| target_task | 対象タスク |
| target_domain | 対象分野・用途 |
| inputs | 入力 |
| outputs | 出力 |
| proposed_method | 提案手法 |
| architecture_summary | アーキテクチャ概要 |
| datasets | データセット |
| metrics | 評価指標 |
| key_results | 主なスコア・結果 |
| baselines | 比較対象 |
| novelty | 新規性 |
| limitations | 制約・弱点 |
| failure_cases | 失敗例・苦手条件 |
| reproducibility | コード・モデル・データ公開状況 |
| important_figures | 重要Figure/Table |
| suggested_reading_order | おすすめ読書順 |

---

## 3.2 AI / LLM / VLM論文向け拡張 Schema

AI系論文では下記を優先表示する。

### A. モデル情報

| フィールド | 内容 |
|---|---|
| model_family | LLM / VLM / MLLM / CNN / ViT / VFM / hybrid 等 |
| base_model | GPT / LLaMA / CLIP / BLIP / ViT / DINOv2 等 |
| backbone | encoder / vision encoder / language model 等 |
| model_size | パラメータ数（報告があれば） |
| modalities | text / image / audio / video 等 |
| architecture_components | 主要モジュール |
| architecture_figure | 代表的なArchitecture Figure |

### B. 学習・適応設定

`learning_regime` は単一値ではなく複数タグを許容する。

- zero-shot
- one-shot
- few-shot
- many-shot
- supervised training
- self-supervised
- fine-tuning
- full fine-tuning
- PEFT
- LoRA
- adapter
- prompt tuning
- prompt learning
- in-context learning
- instruction tuning
- preference tuning
- RLHF / RLAIF
- distillation
- retrieval-augmented
- frozen backbone
- training-free

追加項目:

| フィールド | 内容 |
|---|---|
| trainable_parts | どこを学習するか |
| frozen_parts | 凍結部分 |
| supervision | ラベル / 弱教師 / synthetic / none 等 |
| shots | 何shotか |
| pretraining | 事前学習の有無・方法 |
| adaptation_method | 対象タスクへの適応法 |

### C. 評価

| フィールド | 内容 |
|---|---|
| benchmark_datasets | 評価データセット |
| evaluation_setting | zero-shot / few-shot / cross-domain 等 |
| primary_metrics | 主評価指標 |
| secondary_metrics | 副評価指標 |
| key_scores | 代表スコア |
| best_baseline | 主比較対象 |
| delta_vs_baseline | 差分（論文から確認できる場合のみ） |
| sota_claim | 著者がSOTAを主張しているか |
| ablation_summary | Ablationの主要結論 |

### D. 実装・実用性

| フィールド | 内容 |
|---|---|
| inference_requirements | 推論時に必要なもの |
| training_requirements | 学習時に必要なもの |
| compute_reported | GPU / compute / training time（報告があれば） |
| code_available | コード公開 |
| weights_available | 重み公開 |
| license_notes | ライセンス上の注意（確認できる範囲） |

---

## 3.3 Paper Briefの表示優先度

### 常時表示

- 一言要約
- 研究目的
- 背景・課題
- 対象タスク
- 使用モデル / Base model
- 学習設定
- データセット
- 評価指標
- 主なスコア
- アーキテクチャ概要
- 新規性
- 制約・弱点

### 折りたたみ表示

- 詳細学習条件
- compute
- 実装要件
- Failure cases
- Ablation
- 公開資産
- 推奨読書順

---

## 3.4 「分からない」を明示する

Paper Briefで不明な項目をAIが推測で埋めてはいけない。

状態例:

- `confirmed`: 原文に明示
- `derived`: 複数箇所から妥当な要約
- `not_reported`: 原文に報告なし
- `uncertain`: 抽出に自信がない
- `not_applicable`: 該当しない

例:

```text
学習設定: Few-shot [confirmed]
GPU数: 原文で確認できず [not_reported]
```

---

## 3.5 根拠の保持

Paper Briefの各項目は可能な限り以下へリンクする。

```text
Brief Field
  ├ source_version_id
  ├ block_ids[]
  ├ page
  ├ figure/table refs[]
  └ extraction_status
```

UIでは `📍` から原文へ戻れる。

---

# 4. 読書モード

上位の表示モードは3つ。

## 4.1 日本語版

- 原PDFのページ構造・読み順を可能な限り保持
- 英文本文を日本語へ置換
- Figure / Table / Equation は原物を保持
- Heading / Caption は日本語化
- 日本語Blockと原文Blockの対応を保持
- 日本語段落から原文確認 / AI質問 / メモ

## 4.2 原文PDF

- 原PDFそのものを表示
- ページ移動
- Zoom
- 検索
- 日本語版から対応位置へ移動
- Visual Clip範囲選択

## 4.3 比較

- 原文と日本語を左右表示
- 対応Blockを同時ハイライト
- 同じ意味位置を維持

---

# 5. 原文-日本語対応

基本単位:

```text
TranslationUnit
- source_version_id
- original_block_id
- original_text
- translated_text
- page
- bbox
- heading_path
- translation_status
- provider
- model
- prompt_version
- source_hash
```

日本語表示から原文へ切り替えた場合、ページだけでなく対応Blockを優先して表示する。

---

# 6. AIに聞く

## 6.1 質問対象

- 選択テキスト
- 段落
- Section
- Figure
- Table
- Visual Clip
- 論文全体

## 6.2 AIへ渡すContext

基本:

```text
ユーザー質問
+ 選択した日本語（ある場合）
+ 対応する原文
+ 前後の原文Block
+ Section情報
+ 関連Figure/Table Caption
+ 直近の会話履歴
+ 理解ノートの小さなDigest
```

何をモデルに送ったかを回答履歴に記録する。

---

# 7. 会話を続ける

ConversationThreadを導入する。

```text
ConversationThread
- id
- source_id
- target_type
- target_id
- title
- created_at
- updated_at
```

同じ話題の追質問では直近turnを渡す。

「新しい話題」でContextを分離する。

質問はLLM応答前に保存し、失敗しても消えない。

---

# 8. ハイライト・メモ

## 8.1 ハイライト

- 日本語 / 原文のどちらからでも作成
- 対応Anchorを保持
- 日本語と原文で対応位置を表示

## 8.2 テキストメモ

最低限の種類:

- user_thought
- question
- idea
- implementation_idea
- limitation_note
- action

読書中は分類を強制しない。後から整理可能。

---

# 9. Visual Clip

## 9.1 目的

図・表・数式・任意領域を、画像だけでなく関連本文・日本語訳・メモ・根拠位置とセットで保存する。

## 9.2 作成方法

A. Figure / Table単位

B. PDF上の矩形選択

## 9.3 データ

```text
DocumentAsset
- id
- source_id
- source_version_id
- type: figure | table | equation | region
- page
- bbox
- image_path
- caption_block_ids[]
- related_block_ids[]
- created_at
```

Visual Clip:

```text
VisualClip
- id
- asset_id
- translated_caption
- related_text_snapshot
- user_memo
- section_key
- anchor
- created_at
```

## 9.4 UI

保存ダイアログ:

- 画像Preview
- 種別
- Caption
- 関連本文
- 日本語訳
- 自分のメモ
- 保存先
- `保存`
- `AIにこの図を質問`

---

# 10. 理解ノート

論文1本につき1つ。

初期セクション:

1. 研究背景・課題
2. 研究目的
3. 対象タスク
4. 提案手法
5. 使用モデル
6. 学習設定
7. アーキテクチャ
8. データセット
9. 実験条件
10. 実験結果
11. 新規性
12. 制約・弱点
13. 重要用語
14. 自分の考察
15. 応用・実装アイデア
16. 未解決の疑問
17. 次のアクション

カードはテキストだけでなくVisual Clipを含められる。

---

# 11. 保存レイヤー

情報を3層に分ける。

## Layer 1: Activity Log / 全記録

自動保存:
- Q&A
- ハイライト
- メモ
- Visual Clip
- AIエラー / 再試行

## Layer 2: Understanding Draft / 理解の下書き

AI整理候補。

## Layer 3: Understanding Note / 理解ノート

ユーザーが採用した情報。

AIが勝手に「確定知識」に昇格させない。

---

# 12. 根拠へ戻る

Q&A / Note / Highlight / Visual Clip / Paper Brief から、該当原文へ戻れる。

選択肢:

- 日本語版で開く
- 原文PDFで開く
- 比較で開く

Anchor解決優先度:

1. source_version + block_id
2. bbox / page
3. quote + prefix/suffix
4. page
5. 候補提示

誤った位置へ黙って移動しない。

---

# 13. Paper Map

表示対象:

```text
Abstract
1 Introduction
2 Related Work
3 Method
  3.1 Overview
  3.2 Prompt Learner
4 Experiments
5 Conclusion

Figures
  Fig.1 Overview
  Fig.2 Architecture

Tables
  Table 1 Main Results
  Table 2 Ablation
```

MVPでは:
- Section tree
- Figure/Table一覧
- 現在位置
- クリックジャンプ

を実装する。

---

# 14. 検索

## MVP

論文内検索:
- 原文
- 日本語訳
- Caption
- 自分のメモ

結果から該当位置へ移動。

## 後段

横断検索:
- 複数論文本文
- Brief
- Q&A
- Note
- Visual Clip

---

# 15. 音声

音声はMVPコアの後に追加するが、データモデルは先に対応可能にする。

### 音声質問
録音 → 文字起こし → 修正 → 質問送信

### 音声メモ
録音 → 文字起こし → 自動保存

保持:
- raw transcript
- edited transcript
- AI polished text（任意）

---

# 16. MVP対象

## P0 / MVP

- PDF登録
- Paper Brief
- 日本語版
- 原文PDF
- 比較
- 日本語 ↔ 原文の同位置切替
- Figure/Table/Equationを原PDF由来のまま保持
- AI質問
- 会話継続
- ハイライト
- テキストメモ
- Visual Clip
- 理解ノート
- 根拠へ戻る
- Paper Map
- 論文内検索
- Markdown / JSON出力
- 再訪時の位置・記録復元

## P1

- 音声質問
- 音声メモ
- Figure/Table自動認識精度向上
- 横断検索
- Visual ClipへのVLM質問

## P2

- 図そのものの高度VLM解析
- グラフ数値抽出
- 複数論文比較
- Knowledge Graph
- RCO/RKOS統合

---

# 17. MVP受入条件

| ID | 完了条件 |
|---|---|
| M-01 | PDF登録後、Paper Briefが構造化表示される |
| M-02 | Briefの主要項目から原文根拠へ戻れる |
| M-03 | AI論文で使用モデル・学習設定・データセット・指標・スコア・Architecture・制約が抽出対象になる |
| M-04 | 情報がない項目をAIが推測で埋めず `not_reported` 等で示す |
| M-05 | 日本語版・原文PDF・比較を切り替えられる |
| M-06 | 切替後も意味的に同じ位置を維持できる |
| M-07 | 日本語版でもFigure/Table/Equationを確認できる |
| M-08 | 選択箇所からAIへ質問できる |
| M-09 | 追質問が直前会話を踏まえる |
| M-10 | LLM失敗でも質問が消えない |
| M-11 | ハイライト・テキストメモを保存できる |
| M-12 | PDF矩形範囲をVisual Clipとして画像保存できる |
| M-13 | Visual Clipに関連本文・訳・メモ・Source Anchorを保持できる |
| M-14 | Note/Q&A/Visual Clipから該当原文へ戻れる |
| M-15 | Paper MapからSection/Figure/Tableへ移動できる |
| M-16 | 論文内検索から対応箇所へ移動できる |
| M-17 | 再訪時に読書位置・Note・Q&A・Visual Clipが復元される |
| M-18 | Markdown/JSONでノートと根拠情報を出力できる |

---

# 18. 非目標（MVPでやらない）

- 全PDF形式に対する完全OCR
- 全Figure/Tableの完全自動構造理解
- グラフ数値の完全自動抽出
- 複数ユーザー
- クラウド共同編集
- 複数論文の自動Knowledge Graph
- RCOのExperiment / Decision / Meeting機能

---

# 19. 設計原則

1. **原文を失わない**
2. **日本語は原文に必ず戻れる**
3. **図表を文章より二級扱いにしない**
4. **AIの出力と原文事実を混ぜない**
5. **AIが分からないことを推測で埋めない**
6. **読書中の操作を増やしすぎない**
7. **全部保存と理解ノートを分ける**
8. **後から別AI・別システムへ持ち出せる**
