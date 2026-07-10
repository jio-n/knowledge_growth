# 要件定義

最終更新: 2026-07-11 / 状態: 確定(v1)。元要求は `docs/product/original_brief.md`(ユーザー提供プロンプト全文)を参照。

## 機能要件(MVP) — 完了条件は§30準拠

| ID | 要件 | 実装状態の記録先 |
|----|------|------------------|
| F-01 | PDFアップロード/URL/テキスト・Markdown貼付で資料登録 | implementation_status.md |
| F-02 | 重複検出(content_hash / DOI / arXiv ID / URL)と選択肢提示(既存を開く/強制登録) | 〃 |
| F-03 | 資料ライブラリ: 一覧・検索(タイトル/著者/要約)・読書状態フィルタ・タグ | 〃 |
| F-04 | 読解画面: 左=原文(PDF or ブロック表示)、右=概要/対話/ノートの3タブ。画面遷移なし | 〃 |
| F-05 | 構造化初期抽出(背景/手法/実験/新規性/制約/用語/未解決の問い)を登録時に非同期実行 | 〃 |
| F-06 | テキスト選択→ポップオーバー(質問/説明/初学者向け/詳しく/批判的検討/応用/翻訳/ハイライト/原文保存) | 〃 |
| F-07 | 質問時に選択箇所+前後ブロック+見出し階層+ノートダイジェストをコンテキスト構築(全文投入しない) | 〃 |
| F-08 | LLM回答に model/provider/prompt_id/使用コンテキストを記録。回答は原文事実と区別表示 | 〃 |
| F-09 | 回答の全体または選択部分を、AI提案の保存先セクションへ2クリックで保存 | 〃 |
| F-10 | 理解ノート: セクション別知識項目。出所(原文/AI自動抽出/AI回答/AI編集済/ユーザー)と情報種別をバッジ表示 | 〃 |
| F-11 | 知識項目・質問から原文アンカーへジャンプ(PDF=ページ+引用文検索、Web=ブロックID+引用文) | 〃 |
| F-12 | 段落単位・選択単位の日本語訳(原文直下表示、キャッシュ、原語との対応保持) | 〃 |
| F-13 | 理解ノートのMarkdownプレビュー+ダウンロード。資料単位/全体のJSONエクスポート | 〃 |
| F-14 | 全状態がSQLiteに永続化。資料を閉じて再度開くと前回状態(ノート/質問/翻訳/ハイライト)が復元 | 〃 |
| F-15 | 別資料への切替が同一UI内(ライブラリ or 前後ナビ) | 〃 |
| F-16 | 資料横断検索(タイトル/知識/質問/回答, LIKE検索) | 〃 |
| F-17 | ユーザーメモ(知識項目 origin=user)の追加・編集・セクション移動・削除 | 〃 |
| F-18 | 再解析(自動抽出のみ置換、ユーザー由来項目は不可侵) | 〃 |

## 非機能要件

| ID | 要件 |
|----|------|
| N-01 | ローカルファースト: データは `data/`(SQLite+原本ファイル)のみ。外部送信はLLM API呼び出しの文脈のみ |
| N-02 | LLMプロバイダー交換可能(anthropic / openai_compat / mock)。設定ファイルで切替。SDK非依存 |
| N-03 | APIキーなし(mockプロバイダー)でも全フローが動作しテスト可能 |
| N-04 | プロンプトは `prompts/*.md` にバージョン付きで独立管理 |
| N-05 | セットアップは Python 3.11+ のみ前提(Node不要)。`pip install -r requirements.txt` で起動可能 |
| N-06 | Fable 5なしで継続開発可能: 設計・判断・状態はすべて `docs/` とコードコメントに存在 |
| N-07 | エクスポートで全データ退避可能(Markdown/JSON/原本ファイル) |

## MVP外(将来拡張 — roadmap.md参照)

意味検索(embeddings)、複数資料比較ビュー、引用関係グラフ、Zotero/Obsidian連携、Web差分検出、図表・数式認識、翻訳の全文同期表示、再読リマインド、複数LLM並行、bounding boxアンカー、DOI/arXiv APIメタデータ補完。

## 情報種別・出所の語彙(固定enum — 変更時はdata_model.mdと同時更新)

- origin: `source_quote` | `auto_extract` | `llm` | `llm_edited` | `user`
- info_type: `fact` | `claim` | `result` | `llm_summary` | `llm_interpretation` | `user_thought` | `open_question` | `idea` | `term` | `action` | `translation`
- reading_status: `unread` | `reading` | `read` | `recheck`
- verification: `unverified` | `verified` | `disputed`
