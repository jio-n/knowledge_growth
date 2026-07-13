# Design — Reading Workspace

Kiro spec-driven 規約に沿った技術設計書。`requirements.md` の各要件を **どう実装しているか** と、**どこを拡張できるか** を記述します。

- 実装本体は本ディレクトリの兄弟(`../app/`, `../client/`, `../prompts/`)にあります。この文書はコードのすべてを再説明しません — **契約と設計の意図** に絞ります
- 対応する要件 ID を各節に併記します

---

## 1. コンテキストと目的

本ツールは、以下の一連の読解体験を単一プロセス・単一データストア内で提供します:

```
資料登録 → 構造化抽出 → 原文表示+翻訳 → 選択→質問→回答 → 選択保存 → 理解ノート成長 → エクスポート
```

これらは伝統的には別々のツール(要約AI、翻訳ツール、ノートアプリ、PDF リーダー)に分散しています。本ツールの独自性は、**それらを同一の資料単位で束ね、原文アンカーと出所メタデータで縫い合わせている** 点にあります。

## 2. 技術スタックの選択(要件対応: REQ-NFR-02, REQ-NFR-04)

| レイヤー | 採用 | 理由 |
|----------|------|------|
| バックエンド言語 | Python 3.11+ | wheel が広く配布されていて、ML/研究系との親和性が高い |
| Web フレームワーク | FastAPI + uvicorn | 型付きの薄い API 層。依存が軽い |
| DB | stdlib `sqlite3`(ORM なし) | 単一ファイル。移植・バックアップが `cp` で完結 |
| PDF 抽出 | PyMuPDF (`pymupdf`) | 純粋な wheel 配布。座標情報も取れる(将来拡張余地) |
| Web 抽出 | `readability-lxml` + `beautifulsoup4` | main-content 抽出の定番。ライセンス・依存共に許容範囲 |
| HTTP クライアント | `httpx` | 同期・非同期両対応、SDK 依存を避けるため |
| フロントエンド | 素の ES Modules(ビルド無し) | Node 依存を持ち込まない(REQ-NFR-04) |
| PDF レンダリング | pdf.js(vendored) | ブラウザ内でテキスト選択可能な唯一の実用選択肢 |
| Markdown 描画 | marked.js(vendored) | LLM 出力の Markdown を安全に HTML 化 |

**改良で置き換える場合の注意**: 上記のいずれかを他のライブラリに置き換える改良は可能ですが、以下の 5 つの契約は保持してください:

1. DB スキーマ(§4)
2. REST API(`api_reference.md`)
3. LLMProvider 抽象(§6)
4. SourceAnchor 形状(REQ-ANCHOR-01)
5. mock provider による完全動作(REQ-LLM-02)

## 3. コンポーネント構成(要件対応: REQ-NFR-04)

```
┌──────────────────────────────────────────────────┐
│  Browser (client/): 素の JS + pdf.js + marked   │
└──────────────┬───────────────────────────────────┘
               │  HTTP JSON (REST)
┌──────────────▼───────────────────────────────────┐
│  FastAPI (app/main.py)                           │
│  ┌─────────┬─────────┬─────────┬─────────┐        │
│  │ sources │  qa     │knowledge│ export  │ routes │
│  └────┬────┴────┬────┴────┬────┴────┬────┘        │
│       │         │         │         │             │
│       │    ┌────▼─────┐   │         │             │
│       │    │ context  │   │         │             │
│       │    └────┬─────┘   │         │             │
│       │         │         │         │             │
│  ┌────▼─┐   ┌───▼───┐  ┌──▼────┐ ┌──▼────┐         │
│  │ingest│   │prompts│  │export │ │export │  helpers│
│  └──┬───┘   └───┬───┘  └───────┘ └───────┘         │
│     │           │                                  │
│     │      ┌────▼─────┐                            │
│     │      │   llm    │  ← 3 providers             │
│     │      └────┬─────┘                            │
│     │           │                                  │
│  ┌──▼───────────▼──────┐                           │
│  │      db (SQLite)    │                           │
│  └─────────────────────┘                           │
└──────────────────────────────────────────────────┘
         │                                │
         ▼                                ▼
   data/knowledge.db              data/files/*.pdf, *.html
```

### 3.1 モジュール責任表

| モジュール | 責任 | 依存 |
|-----------|------|------|
| `app/main.py` | FastAPI アプリ組立、routers 束ね、`/api/meta`、静的ファイル配信 | routes_* |
| `app/config.py` | 設定読み込み、パス解決、環境変数上書き | (stdlib) |
| `app/db.py` | SQLite 接続、スキーマ(SCHEMA 文字列)、`get_db()` / `init_db()` / `new_id()` / `now()` | (stdlib) |
| `app/ingest/common.py` | Block データクラス、ハッシュ、DOI/arXiv 抽出、重複検出、heading_path 計算 | (stdlib) |
| `app/ingest/pdf.py` | PDF → Block 列(PyMuPDF)。フォントサイズによる見出し推定 | PyMuPDF |
| `app/ingest/web.py` | URL 取得 → readability → Block 列 + HTML スナップショット | httpx + readability + bs4 |
| `app/ingest/textfile.py` | テキスト/Markdown → Block 列 | (stdlib) |
| `app/llm/base.py` | `LLMProvider` 抽象、`LLMResult`、`strip_json_fences` | (stdlib) |
| `app/llm/anthropic_provider.py` | Anthropic Messages API 直呼び | httpx |
| `app/llm/openai_compat.py` | OpenAI 互換 chat/completions 直呼び | httpx |
| `app/llm/mock.py` | オフライン応答生成(明示ラベル付き) | (stdlib) |
| `app/llm/__init__.py` | `get_provider()` ファクトリ | config, llm/* |
| `app/prompts.py` | `prompts/*.md` ローダー(frontmatter パーサー + プレースホルダ置換) | (stdlib) |
| `app/context.py` | 質問時の context 組み立て(surrounding_context / note_digest / doc_excerpt / context_record) | db |
| `app/analysis.py` | 初期構造化抽出(バックグラウンドスレッド、LLM 失敗時ヒューリスティック) | llm, prompts, context, db |
| `app/routes_sources.py` | `/api/sources/*`(登録 3 種、ライブラリ、詳細、PATCH、DELETE、reanalyze、原本、document) | ingest, analysis, db |
| `app/routes_qa.py` | `/api/sources/{id}/questions`、`/api/translate`、`/api/highlights` | llm, prompts, context, db |
| `app/routes_knowledge.py` | `/api/knowledge*`、`/api/sources/{id}/note`、`/api/search` | llm, prompts, db |
| `app/routes_export.py` | `/api/sources/{id}/export.{md,json}`、`/api/export/all.json` | export, db |
| `app/export.py` | Markdown / JSON 生成ロジック | db |
| `run.py` | エントリポイント。DB 初期化 → uvicorn 起動 | app.main |

## 4. データモデル(要件対応: REQ-SOURCE-*, REQ-KNOW-*, REQ-ANCHOR-*)

### 4.1 エンティティ関係

```
sources 1--* source_versions 1--* document_blocks
sources 1--* questions 1--* answers
sources 1--* knowledge_items      (question_id/answer_id で Q&A に任意リンク)
sources 1--* highlights
sources 1--* translations         (block_id で document_blocks に任意リンク)
sources *--* tags                 (中間: source_tags)
```

### 4.2 テーブル定義

以下はスキーマの契約表です。実装は `app/db.py` の SCHEMA 文字列にあります。**列を変更する PR は、以下の表を同時更新してください**(改良時の原則2)。

#### sources — 資料本体

| 列 | 型 | Nullable | 意味 |
|---|---|---|---|
| id | TEXT PK | no | uuid4 hex 16 文字 |
| type | TEXT | no | `pdf` \| `web` \| `text` \| `markdown` |
| title | TEXT | no | 表示タイトル |
| authors | TEXT | no | JSON 配列(文字列)。空なら "[]" |
| year | INT | yes | |
| venue | TEXT | yes | 現行未使用(将来拡張のため予約) |
| url | TEXT | yes | 元 URL(type=web の場合) |
| canonical_url | TEXT | yes | HTML canonical タグから抽出 |
| doi | TEXT | yes | |
| arxiv_id | TEXT | yes | |
| site_name | TEXT | yes | og:site_name 等 |
| published_at | TEXT | yes | ISO8601 or 元表記 |
| lang | TEXT | yes | 2 文字コード |
| content_hash | TEXT | yes | 重複検出キー |
| file_path | TEXT | yes | `data/files/` 相対パス |
| reading_status | TEXT | no | 既定 `unread`。`unread` \| `reading` \| `read` \| `recheck` |
| importance | INT | no | 既定 0 |
| analysis_status | TEXT | no | 既定 `pending`。`pending` \| `running` \| `done` \| `error` \| `skipped` |
| analysis_error | TEXT | yes | error 時の理由 |
| one_line_summary | TEXT | yes | 初期抽出の一言要約(非正規化キャッシュ) |
| created_at | TEXT | no | ISO8601 UTC |
| updated_at | TEXT | no | ISO8601 UTC |
| last_opened_at | TEXT | yes | ISO8601 UTC |

#### source_versions — 取得版

| 列 | 型 | 意味 |
|---|---|---|
| id | TEXT PK | |
| source_id | TEXT FK | |
| fetched_at | TEXT | ISO8601 |
| content_hash | TEXT | |
| raw_path | TEXT | 原本ファイル相対パス |
| note | TEXT | 任意メモ |

**設計意図**: 現行では 1 資料 = 1 版だが、Web 再取得や PDF 差し替え時のスキーマ受け皿としてテーブルを分けている。

#### document_blocks — 抽出本文の構造単位

| 列 | 型 | 意味 |
|---|---|---|
| id | TEXT PK | |
| version_id | TEXT FK | |
| source_id | TEXT | 冗長格納(検索性能) |
| idx | INT | 版内順序(0 開始) |
| kind | TEXT | `heading` \| `para` \| `code` \| `quote` \| `list` \| `table` \| `figure` |
| level | INT | 見出しレベル |
| text | TEXT | 本文 |
| page | INT | PDF のみ 1 開始 |
| heading_path | TEXT | 所属見出し階層 |

#### questions

| 列 | 意味 |
|---|---|
| id, source_id, version_id | |
| anchor | JSON 文字列(SourceAnchor) |
| selection_text | 選択された原文 |
| prompt_type | `explain` / `explain_simple` / `detail` / `critique` / `apply` / `math` / `free` |
| question_text | 実際に送られた質問文(prompt_type から補完された場合を含む) |
| created_at | |

#### answers

| 列 | 意味 |
|---|---|
| id, question_id | |
| content | Markdown |
| provider | `mock` / `anthropic` / `openai_compat` / ... |
| model | 実モデル名 |
| prompt_id | プロンプトファイルの id |
| prompt_version | frontmatter の version |
| context_summary | JSON: LLM に何を送ったかの要約(監査目的) |
| created_at | |

#### knowledge_items — 理解ノートの実体

| 列 | 意味 |
|---|---|
| id, source_id | |
| section_key | `config.note_template[].key` に対応。テンプレート外の値も許容 |
| title | 任意、~30 字 |
| content | Markdown |
| origin | `source_quote` \| `auto_extract` \| `llm` \| `llm_edited` \| `user` |
| info_type | 語彙 (§4.3) |
| verification | `unverified` \| `verified` \| `disputed` |
| anchor | JSON 文字列(SourceAnchor) |
| question_id, answer_id | 由来 Q&A |
| sort_order | REAL、セクション内の並び。挿入時は末尾 + 1 |
| created_at, updated_at | |

**不変条件**([I-2], [I-3]):
- `origin='auto_extract'` の項目のみ再解析で削除・再生成される
- `origin='llm'` の項目の content を PATCH で変更したら、実装は `origin='llm_edited'` に自動遷移させる

#### tags / source_tags / highlights / translations

`app/db.py` を参照。特筆事項:
- `translations.block_id` が NULL のレコードは「その場限りの選択翻訳」(キャッシュしない)
- `translations.user_edited` フラグでユーザー修正訳を区別
- `highlights.anchor` は SourceAnchor JSON

### 4.3 固定語彙(EARS 要件で言及されているもの)

**enum を追加する場合、以下の3箇所を同時に更新してください**:

| 語彙 | 値 | 追加時に更新する場所 |
|-----|-----|-------------------|
| origin | source_quote / auto_extract / llm / llm_edited / user | 本表、`requirements.md`、`app/routes_knowledge.py`(validation)、UI バッジ色定義(`client/css/app.css`) |
| info_type | fact / claim / result / llm_summary / llm_interpretation / user_thought / open_question / idea / term / action / translation | 本表、`requirements.md`、`app/routes_knowledge.py` の `ALLOWED_INFO_TYPES`、`app/export.py` の `INFO_LABEL` |
| reading_status | unread / reading / read / recheck | 本表、UI セレクタ |
| verification | unverified / verified / disputed | 本表、UI アクション |
| analysis_status | pending / running / done / error / skipped | 本表、UI ポーリング条件 |
| prompt_type | explain / explain_simple / detail / critique / apply / math / free | 本表、`app/routes_qa.py` の `PROMPT_TYPE_PREFIX`、`app/main.py` の `PROMPT_TYPES`、`app/routes_knowledge.py` の `HEURISTIC_MAP` |

## 5. 主要フロー(要件対応: 各要件へマップ)

### 5.1 資料登録フロー(REQ-SOURCE-01/02/03/04, REQ-ANALYSIS-01)

```
Client: POST /api/sources/{pdf|url|text} (body/file)
   │
   ├── ingest.<type>.extract_<type>()  → ExtractedDoc(title, blocks, meta)
   │
   ├── ingest.common.find_duplicates()  → 既存が見つかれば {duplicate: true} を返して終了
   │
   ├── DB: sources / source_versions / document_blocks INSERT
   │        (heading_path はブロック挿入前に assign_heading_paths で埋める)
   │
   ├── 原本 → data/files/<hash>.<ext> 保存
   │
   ├── analysis.run_analysis_async(source_id)  ← 別スレッドで開始
   │
   └── 201 応答: {source: {...}}  (analysis_status='pending')

別スレッド (analysis.run_analysis):
   │
   ├── context.doc_excerpt() で ~24k 字の代表テキスト
   │
   ├── provider.complete_json(initial_extraction プロンプト)
   │      │
   │      ├── 成功 & JSON パース OK → 抽出結果
   │      │
   │      └── 失敗 or 空応答 (mock 含む) → heuristic_extraction() へフォールバック
   │
   ├── 既存 origin='auto_extract' 項目を削除
   │
   ├── 抽出結果を knowledge_items INSERT (origin='auto_extract')
   │
   └── sources.analysis_status='done'、one_line_summary 更新
```

### 5.2 質問応答フロー(REQ-QA-*, REQ-LLM-*)

```
Client: POST /api/sources/{id}/questions {anchor, selection_text, prompt_type, question_text}
   │
   ├── context.surrounding_context(source_id, block_idx)
   │      → (before, after, heading_path)  ± N ブロック、上限 max_context_chars/2 ずつ
   │
   ├── context.note_digest(source_id)  → 直近 knowledge_items 12件を 1 行ずつ
   │
   ├── prompts.get_prompt("answer_question").render(...)
   │
   ├── provider.complete(system, user, hint="answer")  ← Provider 抽象経由
   │
   ├── DB: questions INSERT + answers INSERT
   │      (answers.context_summary に JSON で「何を送ったか」を記録)
   │
   └── 200 応答: {question: {..., answers: [...], anchor: {...}}}
```

**mock provider の挙動**: `answer_question` プロンプトから `---SELECTION---` セクションを抜き出し、モックラベル + 選択文の反響を返す。実回答と誤認できない。

### 5.3 保存フロー(REQ-KNOW-01/02)

```
Client: (ユーザーが 全体を保存 or 選択部分を保存 をクリック)
   │
   ├── 【並行】 POST /api/knowledge/suggest {source_id, content, prompt_type}
   │      │
   │      ├── ヒューリスティック: prompt_type → (section, info_type, title)
   │      │
   │      ├── mock 以外なら LLM で洗練を試みる(失敗しても常に heuristic を返す)
   │      │
   │      └── 200 (never 5xx)
   │
   ├── ユーザーが保存ダイアログで確定 (プリセレクトは自由に変更可)
   │
   ├── POST /api/knowledge {source_id, section_key, content, origin, info_type, anchor, question_id, answer_id, title?}
   │
   └── DB INSERT + ノート即時反映
```

**保存後の origin 遷移** (REQ-KNOW-03):
- ユーザーが後に PATCH で content を変更 & origin=`llm` → 実装で自動的に `llm_edited` へ

### 5.4 アンカー解決フロー(REQ-ANCHOR-02)

原文へのジャンプ動作の擬似コード(クライアント側)。

```javascript
function resolveAnchor(anchor, blocks) {
  // 1. blockId 直参照
  if (anchor.blockId) {
    const el = document.querySelector(`[data-block-id="${anchor.blockId}"]`);
    if (el) { highlightQuoteWithin(el, anchor.quote); scrollAndFlash(el); return; }
  }
  // 2. blockIdx + quote チェック
  if (anchor.blockIdx != null) {
    const el = document.querySelector(`[data-block-idx="${anchor.blockIdx}"]`);
    if (el && contains(el.textContent, anchor.quote)) { highlightAndFlash(el, anchor.quote); return; }
  }
  // 3. quote 全文検索(正規化)
  const hit = findBestBlockByQuote(blocks, anchor.quote, anchor.prefix, anchor.suffix);
  if (hit) { highlightAndFlash(hit.el, anchor.quote); return; }
  // 4. PDF page
  if (anchor.page && isPdfMode()) { scrollToPage(anchor.page); return; }
  // 5. 失敗
  scrollToTop(); toast("原文位置を特定できませんでした");
}
```

**空白正規化**: 連続空白を1つに、前後 trim。日本語は正規化不要。

## 6. LLMProvider 抽象(要件対応: REQ-LLM-*)

### 6.1 インターフェース(`app/llm/base.py`)

```python
class LLMProvider:
    name: str

    def complete(self, system: str, user: str, *,
                 max_tokens: int | None = None,
                 hint: str | None = None) -> LLMResult: ...

    def complete_json(self, system: str, user: str, *,
                      max_tokens: int | None = None) -> LLMResult: ...
```

- `complete_json` はデフォルト実装で system に JSON 強制指示を追加して `complete` を呼ぶ。プロバイダーが JSON mode を持つ場合はオーバーライド可能。
- `hint` は "translate" | "answer" | 未指定 のいずれか。mock だけがこれを見て出力を分ける(実プロバイダーは無視する規約)。
- `LLMResult` = `{text: str, provider: str, model: str}`。

### 6.2 プロバイダーを新しく追加する手順(拡張ポイント)

1. `app/llm/<name>.py` に `class <Name>Provider(LLMProvider)` を実装
2. `app/llm/__init__.py` の `get_provider()` に分岐を追加
3. `config/app.config.json` の `llm` に設定エントリを追加
4. **プロンプトから prompt テンプレートまで既存のまま動くこと** を smoke test で確認
5. `spec/design.md`(この節)に「利用可能なプロバイダー」として追記

### 6.3 mock provider の役割(REQ-LLM-02)

mock は「テスト用」ではなく **一級の provider** です:

- 初期抽出: `complete_json` が `"{}"` を返し、上位が heuristic フォールバックを走らせる
- 質問応答: `complete(hint="answer")` がモック識別子付きの応答を返す
- 翻訳: `complete(hint="translate")` が「【モック翻訳】」プレフィックス付きの原文反響を返す
- 保存先提案: mock 選択時は LLM を呼ばず、常にヒューリスティックを返す(routes_knowledge.py)

## 7. プロンプト管理(要件対応: REQ-LLM-03)

プロンプトは `prompts/*.md` に格納。ローダーは `app/prompts.py`。

### 7.1 ファイル形式

```markdown
---
id: answer_question
version: 1
purpose: 選択箇所を起点とした質問への回答
inputs: title, heading_path, context_before, selection, context_after, note_digest, question
output: markdown
used_by: app/routes_qa.py
---
プロンプト本文... {placeholder} で置換される
```

### 7.2 現行プロンプト

| id | 用途 | 出力形式 | 呼び出し元 |
|----|------|---------|-----------|
| `initial_extraction` | 資料登録時の構造化抽出 | JSON(スキーマは本文参照) | `app/analysis.py` |
| `answer_question` | 選択質問への回答(事実/解釈の区別を指示) | Markdown | `app/routes_qa.py` |
| `translate` | 選択箇所の日本語訳 | プレーンテキスト | `app/routes_qa.py` |
| `suggest_save_target` | 保存先セクション・情報種別・タイトル提案 | JSON | `app/routes_knowledge.py` |

### 7.3 プロンプトを追加する手順(拡張ポイント)

1. `prompts/<id>.md` を frontmatter 付きで作成
2. 呼び出し側で `get_prompt("<id>").render(**vars)` を呼ぶ
3. **本文を変更する場合、必ず `version` を +1**(監査のため。answers.prompt_version に記録される)

## 8. コンテキスト構築(要件対応: REQ-QA-02)

`app/context.py` に集約。ここが LLM への入力量とプライバシー(何が外に出るか)を制御する要衝です。

- `surrounding_context(con, source_id, block_idx)` → (before, after, heading_path)
- `note_digest(con, source_id)` → 直近 `note_digest_items` 件の 1 行要約
- `doc_excerpt(con, source_id, max_chars)` → 初期抽出用(先頭80% + 結論部)
- `context_record(**kwargs)` → answers.context_summary への JSON 記録

**制約**: `config.context.max_context_chars`(既定 8000)を超えない範囲で切り詰め。全文投入は絶対にしない([REQ-QA-02] & 設計思想 §5)。

## 9. アンカー(要件対応: REQ-ANCHOR-01/02/03)

### 9.1 生成(クライアント)

- ブロック表示: 選択範囲を含む DOM 要素(`data-block-id`, `data-block-idx`, `data-page` 属性)から取得。quote = 選択文字列、prefix/suffix = ブロック内の前後
- PDF 表示: 選択範囲のページ番号 + quote + 該当ページ先頭ブロックの `blockIdx` を近似値として保存(bounding box は将来拡張)

### 9.2 保存

DB では JSON 文字列(TEXT 列)。API 応答時にはパース済み dict として返す。**この非対称は意図的**(DB は追加インデックス不要、API は使いやすさ優先)。

### 9.3 解決アルゴリズム

REQ-ANCHOR-02 の 5 段階を厳守。実装は `client/js/views/reader.js` の `resolveAnchor`。改良時、段階の順序は変更しないこと(順序が信頼性の担保)。

## 10. UI 構造(参考 — 詳細は `client/` を参照)

主要な HTTP API との対応関係のみ示す(移植時にフロントを別実装で置き換えるための最小指針):

| 画面 | 主要 API |
|-----|---------|
| ライブラリ(`#/`) | `GET /api/meta`, `GET /api/sources`, `POST /api/sources/*`, `GET /api/search` |
| 読解画面(`#/read/<id>`) | `GET /api/sources/{id}`, `GET /api/sources/{id}/document`, `GET /api/sources/{id}/questions`, `POST /api/sources/{id}/questions`, `POST /api/translate`, `POST /api/highlights`, `DELETE /api/highlights/{id}`, `POST /api/knowledge`, `POST /api/knowledge/suggest`, `GET /api/sources/{id}/note`, `PATCH /api/knowledge/{id}`, `DELETE /api/knowledge/{id}`, `GET /api/sources/{id}/export.md`, `PATCH /api/sources/{id}` |

**アクセシビリティ**: 現行実装は最小限。改良で ARIA を強化することは歓迎(要件を破らない範囲で)。

## 11. 拡張ポイント一覧(要件対応: 全般)

改良を実装コードの深部に加える前に、以下のいずれかで表現できないか確認してください。**リストの順に検討することを推奨します**(上ほど侵襲が少ない)。

| # | 拡張点 | 追加/変更するもの | 影響範囲 |
|---|-------|-------------------|---------|
| 1 | ノートテンプレート(セクション)を変える | `config/app.config.json` の `note_template` | 設定のみ |
| 2 | コンテキストサイズや上限を変える | `config/app.config.json` の `context.*` | 設定のみ |
| 3 | 新しい LLM プロバイダー(§6.2) | `app/llm/<name>.py` + `app/llm/__init__.py` + config | 追加のみ |
| 4 | 新しいプロンプト or 既存を改良(§7.3) | `prompts/<id>.md`(新規または version bump) | 追加のみ |
| 5 | 新しい資料タイプ(例: EPUB, TXT/PDFのハイブリッド) | `app/ingest/<type>.py` + `app/routes_sources.py` に register エンドポイント追加 | 追加中心 |
| 6 | 新しい prompt_type | §4.3 の表に従い 4 箇所を更新 | 語彙拡張 |
| 7 | 新しい info_type / origin | §4.3 の表に従い複数箇所を更新 + UI バッジ | 語彙拡張 |
| 8 | 新しいアンカー段階(例: bounding box) | REQ-ANCHOR-01 のスキーマ拡張 + `resolveAnchor` に新しい段階追加(既存段階の順序は保持) | 追加中心 |
| 9 | 新しい API エンドポイント | 新規 `app/routes_<name>.py` + `app/main.py` include_router + `api_reference.md` 追記 | 追加のみ |
| 10 | 保存後の後処理(例: Obsidian 自動同期) | 新規 `app/hooks/*.py` を作成し、`routes_knowledge.py` の POST 成功時に呼ぶ(hooks レジストリを軽く導入) | 中〜大 |
| 11 | 検索の実装(意味検索) | `GET /api/search` の裏側を差し替え。契約は変えない | 追加/差し替え |
| 12 | Web 再取得と差分検出 | `source_versions` テーブルが既に受け皿。UI と再取得ロジックを追加 | 中 |

## 12. 実行モデルと並行性

- 単一プロセス。1 リクエスト = 1 DB 接続(request scope, WAL モード)
- 分析ジョブは Python の `threading.Thread` で並行実行(単純さ優先)
- **多重分析の抑止は現状 UI 側のみ**。改良で厳密にしたい場合は `sources.analysis_status='running'` を条件に POST /reanalyze を弾く(既知の粗さ)

## 13. セキュリティ考慮

| 項目 | 現状 | 拡張時の注意 |
|-----|------|-------------|
| XSS | 資料本文は textContent、LLM 出力のみ marked | I-11 を厳守 |
| CSRF | localhost 前提のため未対応 | LAN/公開時は必須追加 |
| 認証 | なし | 同上 |
| SQL Injection | すべて parameterized | 生 SQL 文字列組立を禁止 |
| SSRF(URL 登録) | httpx follow_redirects で任意 URL 取得 | 内部ネットワーク宛のブロックが必要な環境ではミドルウェア追加 |
| プロンプトインジェクション | 明示的な対策なし | 改良の余地 |

## 14. パフォーマンス考慮

現状のワークロード(単一ユーザー、資料 ~数百件、ブロック ~数万件)ではボトルネックはほぼ LLM 呼び出しに集中する。以下は将来ボトルネック候補:

- 大規模 SQLite での `LIKE '%q%'` 検索 → FTS5 か embeddings へ切り替え
- Web ページ HTML スナップショットの合計サイズ → 圧縮保存 or 選択的スナップショット
- PDF レンダリングのメモリ消費(全ページ同時レンダリング) → 仮想スクロール

## 15. トレードオフと未解決事項

| 事項 | 現状の選択 | トレードオフ |
|-----|-----------|-------------|
| DB マイグレーション機構 | `CREATE TABLE IF NOT EXISTS` のみ | 追加列は安全。既存列変更は手動対応。改良で本格化するときは Alembic 相当を検討 |
| ストリーミング応答 | 未対応(同期完結) | 長い回答の体感が悪い。SSE 追加は歓迎 |
| PDF レイアウト解析 | フォントサイズヒューリスティック | 2 段組・スキャン PDF で崩れる。座標ベースへの強化余地 |
| プロンプトインジェクション | LLM プロンプトに対する対策なし | 学術資料が対象なので影響は限定的だが、拡張時は要検討 |
| 認証・共有 | 単一ユーザー・ローカル前提 | LAN/クラウド化時は追加設計必須 |

## 16. 移植 / 組み込み時のチェックリスト

本ツールを他システムに切り出して組み込む Kiro / 開発者のためのチェックリスト:

- [ ] Python 3.11+ が利用可能な環境である
- [ ] pip で `requirements.txt`(または相当)がインストール可能
- [ ] `data/` にデータ・原本ファイルを書き込む権限がある
- [ ] LLM を実利用する場合、環境変数(`ANTHROPIC_API_KEY` 等)が設定されている、または `mock` で動かす
- [ ] クライアントを別実装する場合、`api_reference.md` の全エンドポイントの契約を守る
- [ ] ユーザーデータの Markdown/JSON エクスポート機能を残す(思想 §2.5)
- [ ] 資料本文の DOM 挿入は textContent、LLM 出力のみ Markdown レンダリング(I-11)
- [ ] SourceAnchor の生成・保存・解決の 3 段階すべてを実装する(既存の実装を再利用しない場合)

## 17. 参考: 現行ディレクトリ構成

```
app/         FastAPI バックエンド
client/      フロントエンド(vanilla JS, ビルド不要)
prompts/     プロンプト(*.md)
config/      設定(app.config.json)
data/        実行時データ(DB + 原本ファイル。git 管理外)
scripts/     補助スクリプト(vendor 取得等)
tests/       pytest(mock 前提)
spec/        この仕様書パッケージ
```

内部設計(このパッケージには含まないもの)については、リポジトリ内 `docs/` に元開発時の詳細が残っています。参照は任意で、`spec/` の内容と齟齬がある場合は **`spec/` を正とみなしてください**。
