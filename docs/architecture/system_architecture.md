# システムアーキテクチャ

最終更新: 2026-07-11 / 状態: 確定(v1)

## 技術スタック(選定理由は ADR-001)

- **バックエンド**: Python 3.11+ / FastAPI / uvicorn / SQLite(stdlib sqlite3, WALモード, ORMなし)
- **フロントエンド**: ビルド不要 Vanilla JS(ES Modules)+ vendored pdf.js / marked.js。Node不要。
- **LLM**: SDK非依存のHTTPクライアント3種(anthropic / openai_compat / mock)。ADR-003。
- **配布形態**: ローカル単一プロセス。`python run.py` → http://localhost:8300

## レイヤー分離(§25 の分離要求に対応)

```
client/            UI(表示・選択・アンカー作成/解決)
app/routes_*.py    APIエンドポイント(アプリケーションロジック)
app/ingest/        文書解析(pdf/web/textfile → 共通Block列)
app/context.py     コンテキスト構築(選択+前後+見出し+ノートダイジェスト)
prompts/*.md       プロンプトテンプレート(バージョン付き、コード外)
app/llm/           LLM呼び出し(プロバイダー交換可能)
config/app.config.json  モデル設定・コンテキスト上限・ノートテンプレート
app/db.py          永続化(SQLite)
app/export.py      エクスポート(Markdown/JSON)
```

## モジュールマップ

| パス | 責務 |
|------|------|
| `run.py` | エントリポイント(uvicorn起動、初回DB作成) |
| `app/main.py` | FastAPIアプリ組立、静的ファイル配信、/api/meta |
| `app/config.py` | 設定ロード、パス定数(ROOT/DATA_DIR/FILES_DIR/DB_PATH) |
| `app/db.py` | get_db()、init_db()からmigrationを実行、new_id()、now()、v1 SCHEMA互換alias |
| `app/migrations/` | v1 schema snapshot、schema_version履歴、transaction・backup付きrunner、CLI |
| `app/ingest/common.py` | Block/ExtractedDoc、ハッシュ、DOI/arXiv抽出、重複検出 |
| `app/ingest/pdf.py` | PyMuPDF抽出(フォントサイズで見出し推定、段落クラスタリング) |
| `app/ingest/web.py` | httpx取得 + readability本文抽出 + bs4ブロック化 + HTMLスナップショット |
| `app/ingest/textfile.py` | Markdown/テキスト → ブロック |
| `app/llm/base.py` | LLMProvider インターフェース(complete / complete_json, hint引数) |
| `app/llm/mock.py` | オフライン動作用モック(回答に明示ラベル) |
| `app/prompts.py` | prompts/*.md ローダー(frontmatter解析、{placeholder}置換) |
| `app/context.py` | surrounding_context / note_digest / doc_excerpt / context_record |
| `app/analysis.py` | 初期構造化抽出(バックグラウンドスレッド、LLM失敗時ヒューリスティックへフォールバック) |
| `app/routes_sources.py` | 登録(pdf/url/text)・重複検出・一覧・文書・原本・PATCH・削除・再解析 |
| `app/routes_qa.py` | 質問→コンテキスト構築→LLM→回答保存、翻訳(ブロックキャッシュ)、ハイライト |
| `app/routes_knowledge.py` | 知識CRUD、保存先提案、ノート組立、横断検索 |
| `app/routes_export.py` | export.md / export.json / all.json |
| `app/export.py` | Markdown生成(export_spec.md準拠)、JSONダンプ |
| `client/` | UI仕様は ui_spec.md 参照 |
| `tests/` | mockでのE2E API、生成PDF fixtures、DB migration・保全・rollback検証 |

## 主要フロー

### 資料登録(PDF例)
```
POST /api/sources/pdf → hash → 重複検出(→ duplicate応答 or 続行)
→ extract_pdf() → sources+source_versions+document_blocks INSERT
→ 原本を data/files/ へ保存 → run_analysis_async()(別スレッド)
→ 即時 201 応答。クライアントは analysis_status をポーリング
```

### 質問(§9・§10)
```
選択 → クライアントが SourceAnchor 作成 → POST /questions
→ context.py: 選択+前後Nブロック+見出し階層+ノートダイジェスト(全文は投入しない)
→ prompts/answer_question.md をレンダリング → provider.complete()
→ questions/answers INSERT(model・prompt_id・context_summary記録)→ 応答
```

### 保存(§11)
```
回答内テキスト選択(または全体) → 保存ダイアログ
→ POST /api/knowledge/suggest(ヒューリスティック+LLM提案)→ 保存先プリセレクト
→ ユーザーが確定 → POST /api/knowledge(anchor・question_id・origin付き)
→ ノートタブに即時反映
```

## 同時実行・整合性

- SQLite WAL。リクエスト毎に接続を開閉。分析は別スレッド+自前接続。
- 分析の排他: analysis_status で状態管理(pending→running→done/error)。同一資料の多重分析はUI側で抑止(doneまでボタン無効)。

## セキュリティ / プライバシー

- 完全ローカル。外部通信は (1) ユーザーが指定したURLの取得 (2) 設定済みLLM APIへのコンテキスト送信、のみ。
- LLMへ送る内容は answers.context_summary に記録され事後監査可能。
- 認証なし(localhost単一ユーザー前提)。公開サーバーへ置く場合は認証層追加が必要(known_issues.md)。
