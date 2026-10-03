# knowledge_growth 実装タスク一覧 v0.4

最終更新: 2026-10-04
前提仕様: `knowledge_growth_mvp_spec_v0_4.md`
目的: v0.3のReader再設計に、Subscription-first AI runtimeとChatGPT Import Bridgeを追加して実装可能な形にする。

> **優先原則**: API従量課金providerはMVP実装対象外。第一候補はCodex app-server + ChatGPTプラン。Import BridgeはMVP対象。

---

# Phase 0A: AI Runtime Foundation（v0.4追加 / P0）

## T0A-01 AIRuntime interface
- [ ] Paper Brief / translate / question / conversationが同一adapter interfaceを使用
- [ ] UIやDBからprovider固有処理を分離
- [ ] runtime capability metadataを返す

## T0A-02 Codex app-server adapter
- [ ] child process起動・終了
- [ ] stdio RPC initialize / thread / turn処理
- [ ] stream deltaを既存UIへ渡す
- [ ] interruption / error / retryを正規化
- [ ] model catalogを表示用に取得する場合、entitlement確定とは扱わない

## T0A-03 Sign in with ChatGPT / token lifecycle
- [ ] ChatGPTプラン用OAuth認証導線
- [ ] tokenをOSに適した安全な保存領域へ保存（平文config/DB/Git不可）
- [ ] refresh / revoke / sign-out
- [ ] app-server再起動後のthread resume
- [ ] 接続状態 / 利用不可状態をUI表示

## T0A-04 API従量課金を非前提化
- [ ] README/SetupからAPIキー必須に見える記述を除去
- [ ] default runtimeを `codex_chatgpt_plan` または未接続状態に変更
- [ ] API providerはMVP UIから選ばせない
- [ ] APIキー未設定で全MVPテストが実行可能

## T0A-05 no-AI fallback
- [ ] 原PDF閲覧
- [ ] Import済みPaper Brief閲覧
- [ ] メモ / ハイライト / Visual Clip閲覧
- [ ] AI依存操作のみ「未接続」と明示

完了条件: 従量課金APIキーなしでアプリを起動し、Codex app-server接続またはImport Bridgeへ進める。

---

# Phase 2A: ChatGPT Import Bridge（v0.4追加 / P0）

## T2A-01 `.kgpack` schema
- [ ] ZIP互換format
- [ ] manifest.json
- [ ] paper_brief.json
- [ ] evidence_refs.json
- [ ] optional knowledge_items.json / qa_threads.json
- [ ] schema versioning
- [ ] package validation
- [ ] PDF原本はdefaultで含めない

## T2A-02 Source matching
- [ ] source hash
- [ ] DOI / arXiv ID
- [ ] title + authors + year
- [ ] 手動選択fallback
- [ ] mismatch時は自動適用しない

## T2A-03 Portable Evidence Resolver
- [ ] page
- [ ] quote / prefix / suffix
- [ ] heading
- [ ] Figure/Table label
- [ ] local block_id / bboxへ再解決
- [ ] unresolved / ambiguous状態

## T2A-04 Import Preview UI
- [ ] 対象論文
- [ ] Brief field差分
- [ ] Knowledge/Q&A候補
- [ ] Evidence解決率
- [ ] unresolved一覧
- [ ] conflict一覧
- [ ] field/item単位のImport除外

## T2A-05 Provenance / conflict policy
- [ ] `chatgpt_import`等のorigin
- [ ] package_id / generated_by / imported_at
- [ ] generator label / schema version
- [ ] user-edited / origin=userを自動上書きしない
- [ ] imported AI contentをverifiedへ自動昇格しない

## T2A-06 Import API / CLI
- [ ] `POST /api/import/kgpack` または同等endpoint
- [ ] previewとcommitを分離
- [ ] CLI validator（Cloud testでも使用可能）
- [ ] import transaction / rollback

## T2A-07 ChatGPT用Export Template
- [ ] Paper Brief schemaに沿うJSON template
- [ ] evidence refsの必須形式
- [ ] package generator script
- [ ] schema validator fixture

完了条件: ChatGPTで作った`.kgpack`をローカルPDFへ安全に照合し、プレビュー後にPaper Brief等を反映できる。

---

# v0.3実装タスク（継続）

# 0. 実装方針

既存資産を捨てずに拡張する。

再利用対象:
- FastAPI backend
- SQLite
- ResearchSource / source_versions
- document_blocks
- SourceAnchor
- questions / answers
- knowledge_items
- translations
- PDF.js viewer
- Markdown / JSON export
- LLM provider abstraction

大きく追加するもの:
- Paper Brief schema
- PDF bbox / visual asset
- Japanese paper view
- original ↔ Japanese alignment
- ConversationThread
- Visual Clip
- improved context builder
- Paper Map enhancement
- migration mechanism

---

# Phase 0: 現行状態の固定と安全な移行

## T0-01 現行E2E再確認
- [ ] `pytest tests/ -q`
- [ ] PDF登録
- [ ] PDF表示
- [ ] 質問
- [ ] 翻訳
- [ ] ノート保存
- [ ] export
- [ ] 再訪復元

完了条件: 現行機能のbaselineを記録。

## T0-02 DB migration機構
- [ ] schema_versionテーブル追加
- [ ] migration runner追加
- [ ] 現行schemaをv1登録
- [ ] rollback / backup手順文書化

完了条件: 既存data/knowledge.dbを消さずに次schemaへ移行可能。

---

# Phase 1: PDF構造とAnchor強化

## T1-01 PDF block bbox取得
- [ ] `app/ingest/pdf.py` でPyMuPDF block/span座標取得
- [ ] page + bboxを保存
- [ ] 2段組の読み順テストfixture追加
- [ ] Figure/Table/Equation候補のbboxを保持できる下地を作る

## T1-02 document_blocks拡張
追加候補:
- `bbox_json`
- `role`
- `source_hash`
- `parent_block_id`
- `asset_ref`

## T1-03 Anchor resolver拡張
優先順位:
1. version + block_id
2. bbox + page
3. quote/prefix/suffix
4. page
5. 候補提示

完了条件: 再解析後も誤対応を黙って確定しない。

---

# Phase 2: Paper Brief

## T2-01 Paper Brief schema実装
- [ ] Core schema定義
- [ ] AI/LLM/VLM extension定義
- [ ] field status: confirmed / derived / not_reported / uncertain / not_applicable
- [ ] evidence referencesを各fieldに保持

## T2-02 Paper Type分類
- [ ] method
- [ ] benchmark
- [ ] survey
- [ ] dataset
- [ ] analysis
- [ ] system
- [ ] position
- [ ] other

分類でBrief表示項目を少し変える。

## T2-03 AI/LLM論文向け抽出
最低限:
- [ ] objective
- [ ] background/problem
- [ ] target_task
- [ ] target_domain
- [ ] model_family
- [ ] base_model
- [ ] architecture_components
- [ ] zero/few/one-shot等
- [ ] fine-tuning / PEFT / prompt tuning / frozen等
- [ ] datasets
- [ ] metrics
- [ ] key scores
- [ ] baselines
- [ ] novelty
- [ ] limitations
- [ ] important figures/tables

## T2-04 「推測で埋めない」validator
- [ ] unsupported fieldを検知
- [ ] evidenceなしの数値をreject/uncertain化
- [ ] `not_reported` を許容
- [ ] scoreにdataset / metric / settingを可能な限り紐付け

## T2-05 Paper Brief UI
- [ ] 30秒Brief
- [ ] Structured Brief折りたたみ
- [ ] 日本語版で読むCTA
- [ ] 原文PDFを見るCTA
- [ ] 各fieldから📍根拠へ戻る

## T2-06 Brief再解析
- [ ] schema version保存
- [ ] prompt version保存
- [ ] provider/model保存
- [ ] user-corrected fieldを自動上書きしない

---

# Phase 3: 日本語版Reader

## T3-01 TranslationUnit導入
- [ ] original_block_id
- [ ] translated_text
- [ ] page / bbox
- [ ] source_hash
- [ ] provider/model/prompt_version
- [ ] status

## T3-02 Section優先翻訳
- [ ] 表示中sectionから翻訳
- [ ] visible blocks優先
- [ ] 二重翻訳防止
- [ ] stale判定
- [ ] failure単位のretry

## T3-03 Japanese Paper View
- [ ] 原PDFのページ構造を参照した日本語表示
- [ ] Figure/Table/Equation保持
- [ ] Heading/Caption翻訳
- [ ] overflow処理
- [ ] 日本語blockクリック → 原文確認

## T3-04 Original PDF View強化
- [ ] zoom
- [ ] page jump
- [ ] fit width / fit page
- [ ] current semantic position保持

## T3-05 Compare View
- [ ] Original / Japanese左右表示
- [ ] 対応block同時highlight
- [ ] one-scroll / loose-sync設計確認

## T3-06 モード切替位置保持
- [ ] Japanese → PDF
- [ ] PDF → Japanese
- [ ] Compare → Japanese/PDF

完了条件: 同じ意味箇所へ復元。

---

# Phase 4: AI質問・会話

## T4-01 質問先行保存
- [ ] questionをLLM呼出前にDB保存
- [ ] status: pending/running/done/error/cancelled
- [ ] retry relation

## T4-02 ConversationThread
- [ ] threads table
- [ ] target_type / target_id
- [ ] current thread UI
- [ ] new topic

## T4-03 Context Builder v2
範囲:
- [ ] selection
- [ ] original block
- [ ] before/after
- [ ] section
- [ ] related figure/table caption
- [ ] recent thread turns
- [ ] note digest

## T4-04 Context provenance
answersへ記録:
- [ ] source_version
- [ ] block IDs
- [ ] asset IDs
- [ ] thread turn IDs
- [ ] prompt version

## T4-05 Streaming response
- [ ] SSE endpoint
- [ ] provider stream abstraction
- [ ] client incremental rendering
- [ ] 完了/中断時の保存

---

# Phase 5: Highlight / Memo

## T5-01 Highlight cross-view
- [ ] Japanese側で作成
- [ ] Original側で作成
- [ ] same semantic anchor保持

## T5-02 Memo quick capture
- [ ] 読書中は分類不要で保存可能
- [ ] optional type
- [ ] anchor必須
- [ ] 後からsection移動

---

# Phase 6: Visual Clip

## T6-01 DocumentAsset schema
- [ ] asset table
- [ ] type: figure/table/equation/region
- [ ] page/bbox
- [ ] image_path
- [ ] caption refs
- [ ] related block refs

## T6-02 PDF矩形選択UI
- [ ] selection overlay
- [ ] drag resize
- [ ] page boundary制約
- [ ] cancel

## T6-03 高解像度crop
- [ ] 元PDFをPyMuPDFでcrop
- [ ] display screenshotではなく原PDF由来
- [ ] output resolutionルール
- [ ] duplicate detection

## T6-04 関連本文推定
- [ ] bbox近傍
- [ ] caption pattern
- [ ] Fig./Table参照文
- [ ] user correction可

## T6-05 Visual Clip保存UI
- [ ] preview
- [ ] type
- [ ] caption
- [ ] related text
- [ ] translation
- [ ] user memo
- [ ] save destination

## T6-06 Note attachment
- [ ] knowledge_item ↔ asset relation
- [ ] image thumbnail
- [ ] Markdown export image link
- [ ] JSON export metadata

## T6-07 根拠へ戻る
- [ ] Visual Clip → PDF bbox
- [ ] Visual Clip → Japanese related block

---

# Phase 7: Understanding Note

## T7-01 Note section schema更新
セクション:
- 研究背景・課題
- 研究目的
- 対象タスク
- 提案手法
- 使用モデル
- 学習設定
- アーキテクチャ
- データセット
- 実験条件
- 実験結果
- 新規性
- 制約・弱点
- 用語
- 自分の考察
- 応用・実装アイデア
- 未解決
- Action

## T7-02 Layer separation
- [ ] activity log
- [ ] understanding draft
- [ ] understanding note

## T7-03 origin / verification維持
- [ ] source
- [ ] llm
- [ ] llm_edited
- [ ] user
- [ ] auto_extract
- [ ] verification independent from adoption

---

# Phase 8: Paper Map / Search

## T8-01 Paper Map section tree
- [ ] section hierarchy
- [ ] current position
- [ ] jump

## T8-02 Figures/Tables map
- [ ] Figure list
- [ ] Table list
- [ ] page
- [ ] caption
- [ ] jump

## T8-03 In-paper search
検索対象:
- [ ] original blocks
- [ ] translations
- [ ] caption
- [ ] notes

## T8-04 Cross-paper search（P1）
- [ ] Brief
- [ ] Q&A
- [ ] Note
- [ ] Visual Clip
- [ ] semantic searchは後段でも可

---

# Phase 9: Voice（P1）

## T9-01 Voice question
- [ ] microphone permission
- [ ] provider disclosure
- [ ] transcript draft
- [ ] edit before send
- [ ] fallback to keyboard

## T9-02 Voice memo
- [ ] raw transcript
- [ ] edited transcript
- [ ] optional AI polish
- [ ] automatic save after confirmation

---

# Phase 10: Export / Persistence

## T10-01 Markdown export拡張
- [ ] Paper Brief
- [ ] Note
- [ ] Q&A
- [ ] Visual Clip
- [ ] Source anchors

## T10-02 JSON export拡張
- [ ] structured brief
- [ ] asset metadata
- [ ] threads
- [ ] provenance

## T10-03 Reopen restore
- [ ] reading mode
- [ ] semantic position
- [ ] zoom
- [ ] side panel
- [ ] active thread

---

# 推奨実装順

```text
0. Baseline / migration
1. PDF bbox / Anchor
2. Paper Brief
3. Japanese / Original / Compare
4. AI Question + Conversation
5. Highlight / Memo
6. Visual Clip
7. Understanding Note integration
8. Paper Map / Search
9. Voice
10. Export hardening
```

Paper Briefを比較的早く実装する理由:
- 読み始めの価値が大きい
- AI/LLM論文を読む現在のユースケースに直結
- 後続のNote schema / Search / RCO連携でも再利用可能
- 読解UI完成前でも単独で価値検証しやすい

---

# 最初の実装スプリント候補

## Sprint A: Paper Brief MVP
- T0-01
- T0-02
- T2-01〜T2-06

成果:
PDF投入後にAI/LLM論文の構造化Briefが出る。

## Sprint B: Bilingual Reading Foundation
- T1-01〜T1-03
- T3-01〜T3-06

成果:
日本語版・原文PDF・比較を同じ位置で往復できる。

## Sprint C: Read → Ask → Save
- T4
- T5
- T7

成果:
読む途中で質問し、会話し、知識として残せる。

## Sprint D: Visual Knowledge
- T6
- T8

成果:
図表をVisual Clipとして保存し、Paper Map / Searchから再発見できる。

---

# テスト重点

- 2段組PDF
- Architecture Figureを含むAI論文
- 大きなTable
- 数式
- Captionと本文参照
- Zero-shot/Few-shot表現
- score値とdataset/metricの対応
- 論文に書いていない情報をBriefが捏造しないこと
- 切替時の位置保持
- Visual Clipのcrop品質
- LLM失敗時の記録保全
