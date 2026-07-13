# API Reference — Reading Workspace

REST API の完全仕様。**他システムから HTTP 経由で組み込む** 場合、および **クライアントを別実装で置き換える** 場合の契約書です。

- ベース URL 例: `http://127.0.0.1:8300`
- 全エンドポイントの応答は `application/json`(明示された例外あり)
- リクエストボディは `application/json`(登録 PDF のみ multipart/form-data)
- エラーは FastAPI 標準の `{"detail": "<message>"}` 形式(日本語メッセージあり)
- 認証: 現状なし(localhost 想定)。改良で追加する場合は破壊的変更として明示すること

すべてのエンドポイントには対応する要件 ID を併記します。

---

## 目次

- [Meta](#meta)
- [Sources — 登録](#sources--登録)
- [Sources — ライブラリ・詳細](#sources--ライブラリ詳細)
- [Sources — 文書・原本](#sources--文書原本)
- [Sources — 分析](#sources--分析)
- [Questions / Answers](#questions--answers)
- [Translation](#translation)
- [Highlights](#highlights)
- [Knowledge (Note items)](#knowledge-note-items)
- [Understanding Note](#understanding-note)
- [Search](#search)
- [Export](#export)
- [共通スキーマ](#共通スキーマ)

---

## Meta

### GET /api/meta
**要件**: REQ-META-01, REQ-LLM-01(初期化不要制約 [I-14])

**応答 200**:
```json
{
  "provider": "mock",
  "model": "mock",
  "note_template": [
    {"key": "background", "label": "研究背景・課題"},
    ...
  ],
  "prompt_types": [
    {"key": "explain", "label": "分かりやすく説明"},
    {"key": "explain_simple", "label": "初学者向けに説明"},
    {"key": "detail", "label": "専門的に詳しく"},
    {"key": "critique", "label": "批判的に検討"},
    {"key": "apply", "label": "応用を考える"},
    {"key": "math", "label": "数式を分解"},
    {"key": "free", "label": "自由質問"}
  ]
}
```

このエンドポイントは **LLM プロバイダーを初期化しない**。API キー未設定でも常に 200 を返す。

---

## Sources — 登録

### POST /api/sources/pdf
**要件**: REQ-SOURCE-01

**リクエスト** (multipart/form-data):
- `file` (required, PDF binary)
- `force` (optional, bool, default `false`)

**応答 200(登録成功)**:
```json
{ "source": { <SourceRecord> } }
```

**応答 200(重複検出時, `force=false` の場合)**:
```json
{
  "duplicate": true,
  "existing": [
    {"id": "...", "title": "...", "type": "pdf", "created_at": "..."}
  ]
}
```

**応答 400**: PDF ではないファイル(magic bytes 不一致)

---

### POST /api/sources/url
**要件**: REQ-SOURCE-02

**リクエスト**:
```json
{ "url": "https://arxiv.org/abs/1706.03762", "force": false }
```

**応答 200**: `{ "source": { ... } }` または `{ "duplicate": true, "existing": [...] }`

**応答 400**: `url` が http:// / https:// で始まらない
**応答 502**: URL 取得失敗

---

### POST /api/sources/text
**要件**: REQ-SOURCE-03

**リクエスト**:
```json
{
  "content": "本文全体...",
  "title": "任意のタイトル",           // 省略時は抽出タイトルを使用
  "filename": "notes.md",              // 省略可。.md/.markdown で type=markdown
  "force": false
}
```

**応答 200**: `{ "source": { ... } }` または `{ "duplicate": true, "existing": [...] }`
**応答 400**: content が空

---

## Sources — ライブラリ・詳細

### GET /api/sources
**要件**: REQ-SOURCE-05

**クエリパラメータ**:
- `q` (optional): タイトル / 著者 / one_line_summary への部分一致
- `status` (optional): reading_status で絞り込み
- `tag` (optional): タグ名で絞り込み

**応答 200**:
```json
{
  "sources": [
    {
      ...<SourceRecord>,
      "tags": ["ml", "attention"],
      "knowledge_count": 3,        // origin != auto_extract の件数
      "open_question_count": 1,
      "question_count": 5
    }
  ]
}
```

並び順: `last_opened_at` → `updated_at` の降順。

---

### GET /api/sources/{source_id}
**要件**: REQ-SOURCE-05

**副作用**: `last_opened_at` を現在時刻に更新する。

**応答 200**:
```json
{ "source": { ...<SourceRecord>, "tags": [...] } }
```

**応答 404**: 資料が存在しない

---

### PATCH /api/sources/{source_id}
**要件**: REQ-SOURCE-06

**リクエスト**(すべて任意):
```json
{
  "title": "新タイトル",
  "reading_status": "reading",     // unread | reading | read | recheck
  "importance": 2,
  "tags": ["ml", "attention"]      // 指定時は既存タグを全置換
}
```

**応答 200**: 更新後の `{ "source": { ... } }`

**タグ処理の意味論**: `tags` を配列で指定した場合、既存のタグ関連付けを **全削除** して新規セットに置き換える。null(省略)なら変更なし。

---

### DELETE /api/sources/{source_id}
**副作用**: CASCADE で関連する source_versions / document_blocks / questions / answers / knowledge_items / highlights / translations も削除される。原本ファイル(`data/files/*`)は現状削除しない(改良候補)。

**応答 200**: `{"ok": true}`

---

## Sources — 文書・原本

### GET /api/sources/{source_id}/document
**要件**: REQ-DOC-03

表示に必要な情報を一括取得する。

**応答 200**:
```json
{
  "version": { <SourceVersion> },
  "blocks": [ { <Block> }, ... ],
  "translations": {
    "<block_id>": "訳文...",
    ...
  },
  "highlights": [ { <Highlight>, "anchor": {...} }, ... ]
}
```

**注**: highlights の `anchor` は API 応答時点で JSON パース済み(オブジェクト)として返る。DB では JSON 文字列で格納されているが、クライアントは常にオブジェクトを受け取る前提でよい。

---

### GET /api/sources/{source_id}/file
**要件**: REQ-DOC-02

原本 PDF または HTML スナップショットを返す。

**応答 200**:
- Content-Type: `application/pdf`(type=pdf の場合)/ `text/html`(type=web の場合)
- ボディ: バイナリまたは HTML

**応答 404**: 資料が text/markdown、または原本ファイルが失われている

---

## Sources — 分析

### POST /api/sources/{source_id}/reanalyze
**要件**: REQ-ANALYSIS-04

初期構造化抽出を再実行する。**origin='auto_extract' の項目のみ置換**。ユーザー由来項目(origin != auto_extract)は保持される。

**応答 200**: `{"ok": true}`(即時応答。分析は別スレッドで走る)

---

## Questions / Answers

### POST /api/sources/{source_id}/questions
**要件**: REQ-QA-01, REQ-QA-02, REQ-QA-03, REQ-QA-05, REQ-LLM-01

**リクエスト**:
```json
{
  "anchor": { <SourceAnchor> } | null,
  "selection_text": "選択された原文全体...(任意)",
  "prompt_type": "explain",         // explain | explain_simple | detail | critique | apply | math | free
  "question_text": "この式は何を意味しますか?"
}
```

- `question_text` が空文字列で、`prompt_type` が `free` 以外の場合、サーバー側で prompt_type に対応する定型質問文を補完する。
- `anchor.blockIdx` が指定されると、周辺ブロック ± N を LLM コンテキストに含める。

**応答 200**:
```json
{
  "question": {
    "id": "...",
    "source_id": "...",
    "anchor": { <SourceAnchor> } | null,
    "selection_text": "...",
    "prompt_type": "explain",
    "question_text": "...",
    "created_at": "...",
    "answers": [
      {
        "id": "...",
        "content": "回答本文(Markdown)",
        "provider": "mock" | "anthropic" | "openai_compat",
        "model": "mock" | "claude-sonnet-5" | "gpt-4o-mini" | ...,
        "prompt_id": "answer_question",
        "prompt_version": "1",
        "context_summary": "{...json...}",
        "created_at": "...",
        "saved": false                // 既に少なくとも 1 件の知識項目に派生していれば true
      }
    ]
  }
}
```

**応答 404**: source_id が存在しない
**応答 502**: LLM プロバイダー呼び出しに失敗(mock 以外)

---

### GET /api/sources/{source_id}/questions
**要件**: REQ-QA-04

対話タブの復元用。時系列昇順(created_at 昇順)。

**応答 200**:
```json
{ "questions": [ { <QuestionWithAnswers> } ] }
```

---

## Translation

### POST /api/translate
**要件**: REQ-TRANS-01

**リクエスト**:
```json
{
  "source_id": "...",
  "text": "翻訳したい原文",
  "block_id": "..."        // 省略可。指定時はキャッシュを利用/保存
}
```

**応答 200**:
```json
{ "translation": "訳文...", "cached": true | false }
```

- `block_id` 指定 + キャッシュヒット → LLM を呼ばずにキャッシュを返す (`cached: true`)
- `block_id` 指定 + キャッシュミス → LLM 呼び出し + キャッシュ保存
- `block_id` 未指定 → LLM 呼び出し、キャッシュしない (`cached: false`)

**応答 502**: LLM 呼び出し失敗

---

## Highlights

### POST /api/highlights
**要件**: REQ-HL-01

**リクエスト**:
```json
{
  "source_id": "...",
  "anchor": { <SourceAnchor> },
  "color": "yellow",             // yellow | green | blue | red | ... (自由文字列)
  "comment": "任意のコメント"
}
```

**応答 200**: `{"id": "<highlight_id>"}`

---

### DELETE /api/highlights/{highlight_id}
**要件**: REQ-HL-01

**応答 200**: `{"ok": true}`

---

## Knowledge (Note items)

### POST /api/knowledge
**要件**: REQ-KNOW-01

**リクエスト**:
```json
{
  "source_id": "...",
  "section_key": "insights",        // note_template のキー、または任意の文字列
  "title": "任意タイトル",           // 省略可
  "content": "内容(Markdown)",
  "origin": "llm",                   // source_quote | auto_extract | llm | llm_edited | user
  "info_type": "llm_interpretation",// design.md §4.3 の語彙
  "anchor": { <SourceAnchor> } | null,
  "question_id": "..." | null,
  "answer_id": "..." | null
}
```

- `sort_order` はサーバーが自動採番(既存項目の最大 + 1)
- section_key はテンプレート外の値も許容(将来拡張のため)

**応答 200**:
```json
{ "item": { <KnowledgeItemRecord>, "anchor": { ... } | null } }
```

**応答 404**: source_id が存在しない

---

### POST /api/knowledge/suggest
**要件**: REQ-KNOW-02, [I-13]

**リクエスト**:
```json
{
  "source_id": "...",
  "content": "保存したいテキスト",
  "prompt_type": "critique"      // 省略可。設定されていればセクション推定のヒント
}
```

**応答 200**(常に成功):
```json
{ "section_key": "limitations", "info_type": "llm_interpretation", "title": "先頭30字..." }
```

**契約上、この API は 5xx を返さない**。LLM 呼び出しが失敗しても、ヒューリスティック結果を必ず返す。改良でこの契約を破ってはならない。

ヒューリスティックマッピング(現行):
| prompt_type | section_key | info_type |
|-------------|-------------|-----------|
| critique | limitations | llm_interpretation |
| apply | applications | idea |
| translate | terms | translation |
| math | method | llm_interpretation |
| (その他 or null) | insights | llm_interpretation |

---

### PATCH /api/knowledge/{item_id}
**要件**: REQ-KNOW-03, [I-3]

**リクエスト**(すべて任意):
```json
{
  "section_key": "method",
  "title": "...",
  "content": "...",
  "info_type": "...",
  "verification": "verified"       // unverified | verified | disputed
}
```

**副作用**:
- `content` が変更された AND 現在の `origin='llm'` → `origin='llm_edited'` に自動遷移
- `updated_at` を現在時刻に更新

**応答 200**: 更新後の `{ "item": { ... } }`
**応答 404**: 項目が存在しない

---

### DELETE /api/knowledge/{item_id}
**要件**: REQ-KNOW-04

**応答 200**: `{"ok": true}`

---

## Understanding Note

### GET /api/sources/{source_id}/note
**要件**: REQ-KNOW-05

理解ノート全体を、テンプレート順に組み立てて返す。

**応答 200**:
```json
{
  "source": { <SourceRecord> },
  "sections": [
    {
      "key": "background",
      "label": "研究背景・課題",
      "items": [ { <KnowledgeItemWithParsedAnchor> }, ... ]
    },
    {
      "key": "method",
      "label": "提案手法",
      "items": []
    },
    ...
  ],
  "extra_sections": [
    {
      "key": "unknown_key",
      "label": "unknown_key",
      "items": [ ... ]
    }
  ]
}
```

- `sections` は常に config の `note_template` の順番。空でも含まれる(UI が「+追加」を出せるように)
- `extra_sections` はテンプレートに無い section_key を末尾にまとめる

---

## Search

### GET /api/search?q=<query>
**要件**: REQ-SEARCH-01

**応答 200**:
```json
{
  "results": [
    {
      "kind": "source" | "knowledge" | "question" | "answer",
      "source_id": "...",
      "source_title": "...",
      "snippet": "一致箇所±30字",
      "ref_id": "...",              // knowledge/question/answer の場合はその id、source なら source_id
      "section_key": "insights"     // kind=knowledge のときのみ
    }
  ]
}
```

- 検索対象: sources.title / one_line_summary, knowledge_items.title / content, questions.question_text, answers.content
- 検索方式: SQL LIKE(部分一致、大文字小文字非区別)
- 上限 50 件

---

## Export

### GET /api/sources/{source_id}/export.md?download=0|1
**要件**: REQ-EXPORT-01

**応答 200**:
- Content-Type: `text/markdown; charset=utf-8`
- ボディ: Markdown(構造は下記)
- `download=1` の場合、`Content-Disposition: attachment; filename="..."; filename*=UTF-8''...`(RFC 5987)

**Markdown 構造**:
```markdown
---
title: <資料タイトル>
type: pdf|web|text|markdown
authors: [A, B]
year: 2024
url: ...             (該当時)
doi: ...             (該当時)
arxiv_id: ...        (該当時)
tags: [t1, t2]
reading_status: reading
created: <ISO8601>
updated: <ISO8601>
---

# <タイトル>

> **一言要約**: <one_line_summary>

## <セクションラベル>          ← note_template のラベル

- **<タイトル>** — <内容 Markdown>
  - _出所: AI回答 / 種別: AI解釈 / 検証: verified_
  - _原文: p.5 | 3 Method > 3.2 Loss | "quote..."_
- ...

## 質問と回答の履歴

### Q: <質問文>
- _原文: ..._
> <選択箇所抜粋>

**A** _(AI回答 / <model>)_:

<回答本文 Markdown>
```

**規約**:
- 出所ラベルは `app/export.py` の `ORIGIN_LABEL` が正本。改良で追加時は本仕様も更新
- 情報種別ラベルは同 `INFO_LABEL`
- 空セクションは出力しない
- テンプレート外の section_key は section_key そのものを見出しにして末尾に出力

---

### GET /api/sources/{source_id}/export.json
**要件**: REQ-EXPORT-02

**応答 200**: 資料全体の完全ダンプ
```json
{
  "source": { ... },
  "versions": [ ... ],
  "blocks": [ ... ],
  "questions": [ ... ],
  "answers": [ ... ],
  "knowledge_items": [ ... ],
  "highlights": [ ... ],
  "translations": [ ... ],
  "tags": ["ml", ...]
}
```

これに `data/files/<file_path>` を添えれば、資料 1 件の完全退避が成立する(思想 §2.5)。

---

### GET /api/export/all.json
**要件**: REQ-EXPORT-03

**応答 200**:
```json
{
  "exported_at": "<ISO8601>",
  "sources": [ <資料ダンプ>, ... ]
}
```

---

## 共通スキーマ

### SourceRecord

```typescript
{
  id: string,
  type: "pdf" | "web" | "text" | "markdown",
  title: string,
  authors: string,                    // JSON 配列を JSON エンコードした文字列 (例: '["A","B"]')
  year: number | null,
  venue: string | null,
  url: string | null,
  canonical_url: string | null,
  doi: string | null,
  arxiv_id: string | null,
  site_name: string | null,
  published_at: string | null,
  lang: string | null,
  content_hash: string | null,
  file_path: string | null,
  reading_status: "unread" | "reading" | "read" | "recheck",
  importance: number,
  analysis_status: "pending" | "running" | "done" | "error" | "skipped",
  analysis_error: string | null,
  one_line_summary: string | null,
  created_at: string,
  updated_at: string,
  last_opened_at: string | null,
}
```

**注**: `authors` は現状 JSON 文字列で返る(DB 格納形式そのまま)。クライアントは `JSON.parse(source.authors || "[]")` する必要がある。改良で応答時にパース済みで返すのは互換性変更(破壊的)なので慎重に。

### Block

```typescript
{
  id: string,
  version_id: string,
  source_id: string,
  idx: number,                       // 0 開始、版内順序
  kind: "heading" | "para" | "code" | "quote" | "list" | "table" | "figure",
  level: number | null,              // 見出しレベル
  text: string,
  page: number | null,               // PDF のみ 1 開始
  heading_path: string,              // "3 Method > 3.2 Loss"
}
```

### SourceAnchor

```typescript
{
  type: "text-quote",
  quote: string,                     // 最大 500 字
  prefix: string,                    // 最大 60 字。省略可
  suffix: string,                    // 最大 60 字。省略可
  blockId?: string,
  blockIdx?: number,
  page?: number,
  headingPath?: string,
}
```

DB では JSON 文字列。API 応答(GET /note, POST /questions 等)ではパース済みオブジェクトとして返る。

### KnowledgeItemRecord

```typescript
{
  id: string,
  source_id: string,
  section_key: string,
  title: string | null,
  content: string,                   // Markdown
  origin: "source_quote" | "auto_extract" | "llm" | "llm_edited" | "user",
  info_type: "fact" | "claim" | "result" | "llm_summary" | "llm_interpretation"
           | "user_thought" | "open_question" | "idea" | "term" | "action" | "translation",
  verification: "unverified" | "verified" | "disputed",
  anchor: SourceAnchor | null,       // API 応答時はパース済み
  question_id: string | null,
  answer_id: string | null,
  sort_order: number,
  created_at: string,
  updated_at: string,
}
```

### SourceVersion

```typescript
{
  id: string,
  source_id: string,
  fetched_at: string,
  content_hash: string | null,
  raw_path: string | null,
  note: string | null,
}
```

### Highlight

```typescript
{
  id: string,
  source_id: string,
  version_id: string | null,
  anchor: SourceAnchor,              // API 応答時はパース済み
  color: string,
  comment: string | null,
  created_at: string,
}
```

### Translation

```typescript
{
  id: string,
  source_id: string,
  version_id: string | null,
  block_id: string | null,           // null = ephemeral(キャッシュしていない)
  source_text: string,
  translated_text: string,
  provider: string,
  model: string,
  user_edited: number,               // 0 | 1(SQLite の BOOLEAN 慣習)
  updated_at: string,
}
```

---

## エラー応答

FastAPI の標準形式:
```json
{ "detail": "資料が見つかりません" }
```

主なステータスコード:

| コード | 意味 | 例 |
|--------|------|---|
| 400 | リクエスト不正 | PDF でないファイル、空 content、非 http URL |
| 404 | リソース不存在 | 存在しない source_id / item_id |
| 502 | 外部依存(LLM / URL)失敗 | LLM API エラー、URL 取得失敗 |
| 5xx (その他) | 予期しない内部エラー | (改良後) suggest エンドポイントは絶対に返さない |

---

## 変更ログ規約

API を変更したら、以下を同時に行う:

1. `spec/api_reference.md`(本文書)を更新
2. 破壊的変更(既存フィールドの削除、型変更、URL 変更)の場合、`spec/decisions/ADR-<番号>.md` を作成
3. `spec/tasks.md` の関連タスクにチェック
4. `app/main.py` の PROMPT_TYPES など、契約が構造化データとして保持されている箇所を更新
5. E2E スモークテスト(`tests/test_smoke.py`)を通す

**非破壊追加は自由に行ってよい**(新エンドポイント、既存応答への任意フィールド追加、新しい enum 値の許容など)。
