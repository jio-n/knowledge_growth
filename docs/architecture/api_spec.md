# API 仕様

最終更新: 2026-07-11 / 状態: 確定(v1)。実装: `app/routes_*.py`。変更時は本書を同時更新。
すべて JSON(登録PDFのみ multipart)。エラーは FastAPI 標準 `{"detail": "..."}`。

## メタ

### GET /api/meta
```json
{ "provider": "mock", "model": "mock", "note_template": [{"key":"background","label":"研究背景・課題"}, ...],
  "prompt_types": [{"key":"explain","label":"分かりやすく説明"}, ...] }
```

## 資料登録

### POST /api/sources/pdf  (multipart: file, force=bool)
### POST /api/sources/url  `{"url": "...", "force": false}`
### POST /api/sources/text `{"content": "...", "title": null, "filename": null, "force": false}`

成功: `{"source": {<sourcesレコード>}}` — 登録直後 `analysis_status="pending"`。
重複検出時(force=false): `{"duplicate": true, "existing": [{"id","title","type","created_at"}]}` → クライアントは「既存を開く/新規登録(force=true)」の選択UIを出す。

## ライブラリ

### GET /api/sources?q=&status=&tag=
`{"sources": [{...sources列, "tags": ["..."], "knowledge_count": n, "open_question_count": n, "question_count": n}]}`
並び: last_opened_at/updated_at 降順。knowledge_count は origin != 'auto_extract' のみ数える。

### GET /api/sources/{id}
`{"source": {..., "tags": [...]}}`。副作用: last_opened_at 更新。

### PATCH /api/sources/{id}  `{"title"?, "reading_status"?, "importance"?, "tags"?: ["名前", ...]}`
### DELETE /api/sources/{id}
### POST /api/sources/{id}/reanalyze — auto_extract項目を再生成(他origin不可侵)

## 文書

### GET /api/sources/{id}/document
```json
{ "version": {...}, "blocks": [{"id","idx","kind","level","text","page","heading_path"}],
  "translations": {"<block_id>": "訳文"}, "highlights": [{...}] }
```

### GET /api/sources/{id}/file — PDF原本(application/pdf) or HTMLスナップショット

## 質問・翻訳・ハイライト

### POST /api/sources/{id}/questions
```json
{ "anchor": {<SourceAnchor>} | null, "selection_text": "...", 
  "prompt_type": "explain|explain_simple|detail|critique|apply|math|free",
  "question_text": "..." }
```
prompt_type≠free で question_text が空なら、サーバーが定型質問文を補完する。
応答: `{"question": {..., "anchor": {...}, "answers": [{"id","content","provider","model","prompt_id","created_at","saved":bool}]}}`

### GET /api/sources/{id}/questions — `{"questions": [同上]}` 時系列昇順

### POST /api/translate `{"source_id", "text", "block_id": null}`
→ `{"translation": "...", "cached": bool}`。block_id 指定時はキャッシュ・永続化。

### POST /api/highlights `{"source_id", "anchor", "color", "comment"}` → `{"id"}`
### DELETE /api/highlights/{id}

## 知識・ノート

### POST /api/knowledge
```json
{ "source_id": "...", "section_key": "insights", "title": null, "content": "...",
  "origin": "llm|user|source_quote|llm_edited", "info_type": "...",
  "anchor": {...}|null, "question_id": null, "answer_id": null }
```
→ `{"item": {...}}`(sort_order はセクション末尾+1 をサーバーが採番)

### POST /api/knowledge/suggest `{"source_id", "content", "prompt_type": null}`
→ `{"section_key", "info_type", "title"}`。まずヒューリスティック(prompt_type→セクションのマップ: critique→limitations, apply→applications, translate→terms, 他→insights)。プロバイダーがmock以外なら prompts/suggest_save_target.md でLLM提案を試み、成功すれば上書き。失敗時はヒューリスティック結果を返す(この API は絶対に 5xx を返さない)。

### GET /api/sources/{id}/note
```json
{ "source": {...}, "sections": [{"key","label","items":[{<knowledge_item>, "anchor": {...}|null}]}],
  "extra_sections": [...] }
```
note_template 順。空セクションも含める(クライアントが「+追加」を出すため)。テンプレート外 section_key は extra_sections。

### PATCH /api/knowledge/{id} `{"section_key"?, "title"?, "content"?, "info_type"?, "verification"?}`
content変更時、origin が `llm` なら自動的に `llm_edited` へ。→ `{"item": {...}}`

### DELETE /api/knowledge/{id}

## 検索・エクスポート

### GET /api/search?q=
```json
{ "results": [{"kind": "source|knowledge|question|answer", "source_id", "source_title",
               "snippet": "一致箇所±60字", "ref_id": "項目id", "section_key"?: "..."}] }
```
LIKE検索。対象: sources.title/one_line_summary、knowledge_items.title/content、questions.question_text、answers.content。上限50件。

### GET /api/sources/{id}/export.md?download=0|1 — text/markdown(export_spec.md準拠)
### GET /api/sources/{id}/export.json — 資料の全データダンプ
### GET /api/export/all.json — 全資料ダンプ(§19の全退避)

## Import Bridge backend / Paper Brief

`POST /api/import/kgpack/validate`、`POST /api/import/kgpack/preview`（multipart file/source_id/exclude_fields）、
`POST /api/import/kgpack/commit`（preview_id/confirmed）を分離する。
`GET /api/sources/{id}/paper-brief`で取得、
`PATCH /api/sources/{id}/paper-brief/fields/{name}`でユーザー編集を保存する。
[request/response・status・競合契約](../redesign/v0.4/paper_brief_import_foundation.md#import-flow--api)を参照。
