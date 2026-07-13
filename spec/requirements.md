# Requirements — Reading Workspace

Kiro spec-driven 規約に沿った要件文書。**受け入れ基準の正本** です。改良後の PR が「要件を満たしているか」を判定するときは、この文書の該当項目を参照します。

- 記法: **EARS**(Easy Approach to Requirements Syntax)。日本語文中に EARS のキーワード(WHEN / WHILE / IF-THEN / WHERE / SHALL)を大文字で挿入
- 対象範囲: ツール本体の外部から観測可能な振る舞い(内部構造は `design.md`)
- 要件 ID 規約: `REQ-<領域>-<番号>`。改良で ID を再利用・削除してはならず、廃止時は「Deprecated」ラベルを付ける

---

## User Stories(受入基準の背景)

### US-1. 資料の登録と再訪
研究資料を読むユーザーとして、PDF・Web ページ・テキストを登録し、後で再訪したときに前回の理解の続きから作業を再開したい。同一資料の重複登録は明示的に警告してほしい。

### US-2. 選択箇所を起点とした対話
資料を読み進めながら、分からない箇所を選択して即座に LLM に質問したい。回答は「資料に書かれた事実」と「LLM の解釈」が区別されていて、根拠原文がわかる形で欲しい。

### US-3. 選択的な保存とノート育成
LLM の回答のうち、有用な部分だけを最小の手数で理解ノートへ保存したい。保存先は AI が提案してよいが、私が変更できる。保存された知識は資料ごとにセクション別で表示され、原文にワンクリックで戻れる。

### US-4. 出所と情報種別の区別
理解ノートを見返すとき、どれが原文の事実で、どれが LLM の解釈で、どれが自分の考察か、一目で区別したい。

### US-5. データの持ち出し
蓄積した知識を Obsidian など他のツールで使えるよう Markdown で出力したい。ツールが使えなくなっても知識は失われないようにしたい。

### US-6. LLM プロバイダーの選択
私は API キーを持っていなくても本ツールの全機能を試したい。実 LLM を使うときはプロバイダーを選択したい。

---

## Requirements

### 領域: SOURCE(資料の登録・重複検出・ライブラリ)

#### REQ-SOURCE-01: PDF 登録
The system SHALL accept a PDF file upload (multipart) and create a Source with type=`pdf`.

- WHEN 有効な PDF(magic bytes `%PDF-`)がアップロードされ、既存資料と content_hash が一致しない、THEN the system SHALL 新しい Source を作成し、抽出したブロック列と一緒に永続化し、`analysis_status='pending'` の状態で応答する。
- IF アップロードが PDF でない、THEN the system SHALL 400 エラーを返す。
- IF 既存資料と content_hash が一致する AND `force=false`、THEN the system SHALL 新しい Source を作成せず、`{duplicate: true, existing: [...]}` を返す。
- WHERE `force=true` が指定された、the system SHALL 重複チェックをスキップして新しい Source を作成する。

#### REQ-SOURCE-02: URL 登録(Web ページ / arXiv)
The system SHALL accept an http(s) URL, fetch the page, and create a Source with type=`web`.

- WHEN URL が http:// または https:// で始まる、THEN the system SHALL 当該URLを取得し、readability による本文抽出を経てブロック列を作成する。
- The system SHALL 取得時点の HTML を `data/files/<hash>_<date>.html` として保存する(§ [I-5])。
- IF ページが arXiv abs URL を含む、THEN the system SHALL arxiv_id を Source メタデータに保存する。
- The system SHALL メタデータから DOI、canonical_url、著者、公開日、サイト名を可能な範囲で抽出する。
- IF URL 取得に失敗する、THEN the system SHALL 502 エラーを返す(登録は失敗する)。

#### REQ-SOURCE-03: テキスト / Markdown 登録
The system SHALL accept a plain text or Markdown body and create a Source with type=`text` or `markdown`.

- WHEN filename が `.md` または `.markdown` で終わる、THEN type は `markdown`。それ以外は `text`。
- WHEN 明示的な title が指定された、THEN the system SHALL 抽出されたタイトルではなくその title を使う。

#### REQ-SOURCE-04: 重複検出
The system SHALL detect duplicate registration by checking content_hash, DOI, arxiv_id, and canonical URL.

- The system SHALL 上記のいずれかが既存資料と一致する場合を重複と判定する。
- The system SHALL 重複判定時、`{duplicate: true, existing: [{id, title, type, created_at}]}` の形式で候補を返す。
- The system SHALL 重複時、既存資料を勝手に開いたり書き換えたりしない(ユーザーの判断を求める)。

#### REQ-SOURCE-05: 資料ライブラリ
The system SHALL provide a listing of all registered sources.

- WHEN ライブラリが要求された、THEN the system SHALL タイトル・種別・著者・年・要約・タグ・読書状態・知識数・質問数・未解決数・最終閲覧日時を含む一覧を返す。
- The system SHALL last_opened_at → updated_at の降順で並べる。
- WHERE `q` パラメータが指定された、the system SHALL タイトル・著者・要約に対する部分一致で絞り込む。
- WHERE `status` パラメータが指定された、the system SHALL 読書状態で絞り込む。
- WHERE `tag` パラメータが指定された、the system SHALL 当該タグを持つ資料に絞り込む。

#### REQ-SOURCE-06: 資料メタデータ更新
The system SHALL allow updating title, reading_status, importance, and tags of a source.

- WHEN tags が指定された、THEN the system SHALL 既存タグ関連付けを全削除し、新しいタグを付け直す。存在しないタグ名は新規作成する。

---

### 領域: DOC(資料本文と表示)

#### REQ-DOC-01: ブロック抽出
The system SHALL normalize every source type into an ordered list of Blocks.

- Each block SHALL have: `id`, `idx`(0-based順序), `kind`(heading / para / code / quote / list / table / figure), `level`(見出しレベル), `text`, `page`(PDFのみ 1-based)。
- The system SHALL 各ブロックに heading_path(所属見出し階層、例 "3 Method > 3.2 Loss")を付与する。
- IF PDF から抽出できるブロックが 0 件、THEN the system SHALL Source を作成するが analysis_status=`error` とする(ユーザーに再解析を促す)。

#### REQ-DOC-02: 原本ファイルの提供
The system SHALL serve the original PDF (or web snapshot HTML) via `GET /api/sources/{id}/file`.

- WHEN 資料の type=`pdf`、THEN media_type=`application/pdf`。
- WHEN 資料の type=`web`、THEN media_type=`text/html`。
- WHEN 資料の type=`text` or `markdown`、THEN this endpoint SHALL return 404。

#### REQ-DOC-03: 文書取得(表示用)
The system SHALL provide `GET /api/sources/{id}/document` returning `{version, blocks, translations, highlights}`.

- The system SHALL 最新の source_version を返す。
- translations SHALL be a map of `{block_id: translated_text}`。
- highlights SHALL include `id, anchor, color, comment`。

---

### 領域: ANALYSIS(初期構造化抽出)

#### REQ-ANALYSIS-01: 非同期の初期抽出
WHEN a new source is registered, the system SHALL kick off a background analysis job.

- The job SHALL 登録直後 `analysis_status='running'` に遷移する。
- WHILE job が動作中、the system SHALL `GET /api/sources/{id}` で `analysis_status` を取得可能にする。
- WHEN job が正常終了、THEN status=`done` かつ `one_line_summary` が設定される。
- IF job が失敗、THEN status=`error` かつ `analysis_error` に理由が記録される(ユーザーは再解析可能)。

#### REQ-ANALYSIS-02: 抽出結果の保存位置
The system SHALL store initial extraction as knowledge_items with `origin='auto_extract'`.

- 抽出項目は note_template のセクション(background / method / experiments / novelty / limitations / terms / questions / ...)に配分される。
- 再解析実行時、origin=`auto_extract` の項目のみ削除・再生成される(他 origin は不可侵)。

#### REQ-ANALYSIS-03: LLM 失敗時のフォールバック
IF the LLM call for initial extraction fails or returns unusable JSON, THEN the system SHALL fall back to a heuristic extraction (見出し構成 + 冒頭段落) rather than fail the registration.

- フォールバック時、`open_questions` に「ヒューリスティック抽出で実行された」旨を記録する。

#### REQ-ANALYSIS-04: 再解析
The system SHALL provide `POST /api/sources/{id}/reanalyze` to re-run initial extraction.

- The endpoint SHALL 既存の auto_extract 項目のみ置換する。
- The endpoint SHALL user 由来項目(origin != auto_extract)を絶対に変更しない。

---

### 領域: QA(選択箇所を起点とした質問応答)

#### REQ-QA-01: 質問の送信
The system SHALL accept a question tied to an optional SourceAnchor and selection_text.

- Request body SHALL contain: `anchor` (nullable), `selection_text` (nullable), `prompt_type` (enum: explain / explain_simple / detail / critique / apply / math / free), `question_text`。
- WHEN prompt_type ≠ `free` AND question_text が空、THEN the system SHALL prompt_type に対応する定型質問文を自動補完する。

#### REQ-QA-02: コンテキスト構築
WHEN answering a question, the system SHALL construct context from selection + surrounding blocks + heading path + note digest, and NOT the full document.

- Context SHALL 選択ブロック ± `context.surrounding_blocks`(config 値、既定 2)を含む。
- Context SHALL note_template セクション別に直近 `context.note_digest_items`(既定 12)件の知識項目を 1 行ずつダイジェスト化して含める。
- Total context SHALL not exceed `context.max_context_chars`(既定 8000)。
- The system SHALL 上記の要約情報を `answers.context_summary` (JSON) に必ず記録する。

#### REQ-QA-03: 回答の記録
WHEN an answer is generated, the system SHALL persist provider, model, prompt_id, prompt_version, context_summary, created_at.

- これらは表示(監査目的)と再現(誰がどのモデルで答えたか)のために必須である。

#### REQ-QA-04: 回答保存状態の追跡
The system SHALL indicate for each answer whether at least one knowledge_item derives from it.

- `GET /api/sources/{id}/questions` の返り値の各 answer に `saved: bool` が含まれる。

#### REQ-QA-05: LLM エラー処理
IF the LLM provider raises during question answering, THEN the system SHALL return 502 with the provider error message, and the client SHALL provide a retry action.

- WHERE provider=`mock`、the system SHALL never raise LLM errors。

---

### 領域: TRANS(翻訳)

#### REQ-TRANS-01: 選択箇所またはブロックの翻訳
The system SHALL accept `POST /api/translate {source_id, text, block_id?}` and return `{translation, cached}`。

- WHEN block_id が指定された AND 当該ブロックの翻訳が DB キャッシュに存在する、THEN the system SHALL LLM を呼ばずにキャッシュを返す(cached=true)。
- WHEN block_id が指定された AND キャッシュが無い、THEN the system SHALL LLM 呼び出し結果をキャッシュに保存する。
- WHEN block_id が指定されていない、THEN the system SHALL 一時翻訳とし、キャッシュしない。

#### REQ-TRANS-02: ユーザー編集訳の区別
The system SHALL distinguish AI translations from user-edited translations via `user_edited` flag on translations records.

---

### 領域: KNOWLEDGE(知識項目 CRUD と保存先提案)

#### REQ-KNOW-01: 知識項目の作成
The system SHALL accept `POST /api/knowledge` and create a knowledge_item with mandatory origin and info_type.

- Request body SHALL contain: `source_id, section_key, content, origin, info_type, title?, anchor?, question_id?, answer_id?`。
- origin SHALL be one of: `source_quote | auto_extract | llm | llm_edited | user`。
- info_type SHALL be one of the vocabulary defined in `design.md` (fact / claim / result / llm_summary / llm_interpretation / user_thought / open_question / idea / term / action / translation)。
- The system SHALL sort_order を「対象資料の対象セクション内の既存 sort_order の最大値 + 1」で自動採番する。
- The system SHALL 未知のセクション key を許容する(拡張性)。

#### REQ-KNOW-02: 保存先提案
The system SHALL provide `POST /api/knowledge/suggest {source_id, content, prompt_type?}` returning `{section_key, info_type, title}`。

- The system SHALL まずヒューリスティック(prompt_type → セクション対応)で結果を用意する。
- WHERE 現在の LLM プロバイダーが `mock` 以外、the system SHALL LLM 提案で結果を洗練する試みを行う。
- IF LLM 提案の JSON が無効、または LLM 呼び出しが例外を投げる、THEN the system SHALL ヒューリスティック結果をそのまま返す。
- **The endpoint SHALL never return 5xx**([I-13])。

#### REQ-KNOW-03: 知識項目の更新
The system SHALL accept `PATCH /api/knowledge/{id}` with any subset of `section_key, title, content, info_type, verification`。

- WHEN `content` が変更された AND 対象項目の origin が `llm`、THEN the system SHALL origin を `llm_edited` に自動遷移させる([I-3])。
- The system SHALL updated_at を現在時刻に更新する。

#### REQ-KNOW-04: 知識項目の削除
The system SHALL accept `DELETE /api/knowledge/{id}` and remove the record。

#### REQ-KNOW-05: 理解ノートの取得
The system SHALL provide `GET /api/sources/{id}/note` returning `{source, sections, extra_sections}`。

- sections SHALL be in the order of `config.note_template`, each with `{key, label, items[]}`。
- 空のセクションも含めて返す(UI が「+追加」を出せるようにするため)。
- テンプレートに含まれないセクション key に属する項目は extra_sections として末尾に返す。

---

### 領域: ANCHOR(原文アンカーの生成と解決)

#### REQ-ANCHOR-01: アンカー構造
Every SourceAnchor SHALL follow the JSON shape:
```
{
  "type": "text-quote",
  "quote":  string (max 500),
  "prefix": string (max 60),
  "suffix": string (max 60),
  "blockId":  string?,
  "blockIdx": int?,
  "page":     int?,
  "headingPath": string?
}
```
All fields except `type` MAY be null / absent(ベストエフォート)。

#### REQ-ANCHOR-02: 解決アルゴリズム(クライアント)
WHEN a user activates an anchor (e.g., clicks 📍 from a knowledge item), the client SHALL resolve it by trying, in order:

1. `blockId` が現行版に存在 → そのブロックへスクロールし、ブロック内で quote を部分一致ハイライト。
2. `blockIdx` の範囲内で quote(先頭80字、空白正規化)が含まれる → 同上。
3. quote 全文検索(空白正規化)。複数一致時は prefix / suffix 一致度で選ぶ。
4. `page`(PDF)→ 当該ページ先頭へスクロール。
5. すべて失敗 → 資料先頭に戻り、「原文位置を特定できませんでした」の通知を出す(**知識項目自体は失われない**)。

#### REQ-ANCHOR-03: アンカーの永続化
Anchors SHALL be stored as JSON strings in DB columns and returned as parsed dicts in API responses(`GET /api/sources/{id}/note` 等)。

---

### 領域: HIGHLIGHT

#### REQ-HL-01: ハイライトの作成・削除
The system SHALL accept `POST /api/highlights {source_id, anchor, color, comment?}` and `DELETE /api/highlights/{id}`。

#### REQ-HL-02: ハイライトの取得
The system SHALL return all highlights of a source via `GET /api/sources/{id}/document`。

---

### 領域: SEARCH

#### REQ-SEARCH-01: 横断検索
The system SHALL provide `GET /api/search?q=` returning at most 50 results across sources, knowledge_items, questions, answers.

- Each result SHALL contain: `kind` (source / knowledge / question / answer), `source_id`, `source_title`, `snippet` (60 chars around the match), `ref_id`, and (for knowledge) `section_key`。
- 検索は SQL LIKE(大文字小文字非区別、部分一致)で行う。
- MVP では意味検索は含まない(将来拡張)。

---

### 領域: EXPORT

#### REQ-EXPORT-01: Markdown エクスポート
The system SHALL provide `GET /api/sources/{id}/export.md?download=0|1` returning a Markdown document following the spec in `design.md` (YAML frontmatter + セクション別 knowledge_items + Q&A 履歴 + 出所ラベル + アンカー参照)。

- WHEN `download=1`、THEN the response SHALL include `Content-Disposition: attachment; filename="..."; filename*=UTF-8''...`(RFC 5987 準拠、非 ASCII タイトル対応)。
- The Markdown SHALL contain origin label and info_type label for every item(§ [I-3] を外部でも保持)。

#### REQ-EXPORT-02: JSON エクスポート(単一資料)
The system SHALL provide `GET /api/sources/{id}/export.json` returning a complete dump of the source (source, versions, blocks, questions, answers, knowledge_items, highlights, translations, tags)。

#### REQ-EXPORT-03: 全体 JSON エクスポート
The system SHALL provide `GET /api/export/all.json` returning `{exported_at, sources: [<same shape as export.json>...]}`。

---

### 領域: LLM(プロバイダー抽象)

#### REQ-LLM-01: プロバイダー選択
The system SHALL support at least 3 provider implementations: `mock`, `anthropic`(Messages API 直呼び), `openai_compat`(OpenAI compatible chat/completions endpoint)。

- WHERE 環境変数 `KG_LLM_PROVIDER` が設定されている、the system SHALL config ファイルより優先する。
- The system SHALL 設定値または環境変数で選択されたプロバイダーの初期化に失敗した場合(API キー未設定など)、LLM 呼び出し時にのみ日本語エラーを返す。`/api/meta` などプロバイダー非依存のエンドポイントは影響を受けない([I-14])。

#### REQ-LLM-02: mock プロバイダーの完全性
The system SHALL provide a `mock` provider that requires no network, no API key, and no external dependencies.

- WHEN provider=`mock`、THEN すべての LLM 依存機能(質問応答、翻訳、初期抽出、保存先提案)は動作する。
- 初期抽出はヒューリスティックへフォールバックする(REQ-ANALYSIS-03)。
- 翻訳・回答は「モック回答」であることを本文中に明示する接頭辞を含む([I-9])。

#### REQ-LLM-03: プロンプトの外部管理
All prompts sent to LLMs SHALL be loaded from `prompts/*.md` files with frontmatter (id, version, purpose)。

- Prompt bodies SHALL not be hardcoded in Python code([I-8])。
- Prompt files SHALL have their `version` bumped when the body is changed(監査目的)。

---

### 領域: PERSIST(永続化と再訪)

#### REQ-PERSIST-01: 単一 SQLite ファイル
The system SHALL store all structured data in a single SQLite file (`data/knowledge.db` by default).

- The path SHALL be overridable via environment variable `KG_DATA_DIR`(テスト・複数プロファイル用途)。
- The DB SHALL use WAL journal mode。
- The DB SHALL enforce foreign keys ON。

#### REQ-PERSIST-02: 再訪時の状態復元
WHEN a user re-opens a previously visited source, the system SHALL restore all persisted state (questions, answers, translations, highlights, knowledge items, reading status).

- 表示状態のうち、タブ選択・ペイン幅・スクロール位置は localStorage(クライアント側)に保持される([I-12])。

#### REQ-PERSIST-03: 原本ファイルの保存
The system SHALL save the original PDF or web HTML snapshot to `data/files/` and reference it via `sources.file_path`(relative path)。

---

### 領域: META

#### REQ-META-01: メタ情報
The system SHALL provide `GET /api/meta` returning `{provider, model, note_template, prompt_types}`。

- This endpoint SHALL not instantiate an LLM provider([I-14])。
- `provider` SHALL be the currently configured provider name。
- `model` SHALL be that provider's model name (or "mock" for mock)。

---

## Non-Functional Requirements

### REQ-NFR-01: ローカルファースト
- All data SHALL reside locally on the user's machine。
- The only outbound network calls SHALL be: (a) user-specified URL fetches for web-source registration, (b) configured LLM API calls。

### REQ-NFR-02: セットアップ最小要件
- Setup SHALL require only Python 3.11+ and pip (no Node.js, no compiler toolchain beyond wheels)。
- Launch command SHALL be a single command: `python run.py`。

### REQ-NFR-03: テスト
- `pytest tests/` SHALL pass without any API key or network access(mock provider + temp DB)。
- The E2E smoke test SHALL cover: register → analyze → question → answer → save → note → export → search → duplicate detection。

### REQ-NFR-04: 移植性
- The system SHALL not depend on OS-specific features beyond standard filesystem paths。
- Frontend SHALL not require any build step (vanilla JS + vendored libraries)。

### REQ-NFR-05: セキュリティ(XSS)
- Rendering of source document text into the DOM SHALL use `textContent` only, never `innerHTML`([I-11])。
- Rendering of LLM output MAY use Markdown parsing (marked)。

---

## Acceptance Test Matrix

以下の E2E 経路が通ることが最終受け入れ基準です:

| # | 経路 | 検証済み |
|---|------|----------|
| AT-1 | Markdown を貼付登録 → 初期抽出 done → GET note に auto_extract 項目 | pytest |
| AT-2 | 選択+アンカー付き質問 → 回答に mock ラベル → 保存 → note に origin=llm 項目 | pytest |
| AT-3 | 保存済み項目の content を PATCH → origin が llm_edited に遷移 | pytest |
| AT-4 | export.md に出所ラベルと Q&A 履歴が含まれる | pytest |
| AT-5 | 横断検索でノート内容がヒット | pytest |
| AT-6 | 同一内容を再登録 → duplicate 応答 | pytest |
| AT-7 | export/all.json ですべての資料がダンプされる | pytest |
| AT-8 | PDF アップロード → analysis_status=done → 抽出ブロックあり | 手動 |
| AT-9 | 実 arXiv abs URL 取得 → arxiv_id / authors / blocks 抽出 | 手動 |
| AT-10 | ブラウザ再読込後、質問・翻訳・ハイライト・ノートが復元 | 手動 |

## Deprecated Requirements

(なし)

## 改良時のガイダンス

- 新しい要件を追加する場合、既存の REQ-* を再利用可能なら新規発番せずそこに条件を足す
- 既存の要件を削除する場合、ID は残して「**Deprecated (reason: ..., replaced by REQ-...)**」ラベルを付ける(下流の実装ノートやチケットが ID を参照している可能性があるため)
- 要件間の依存(A が B の受け入れ基準を含意する)は `→ REQ-XXX-YY` の形で相互参照する
