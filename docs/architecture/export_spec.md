# エクスポート仕様

最終更新: 2026-07-11 / 実装: `app/export.py`

## Markdown(GET /api/sources/{id}/export.md)

Obsidian等でそのまま使える単一ファイル。構造:

```markdown
---
title: <資料タイトル>
type: pdf|web|text|markdown
authors: [A, B]
year: 2024
url: / doi: / arxiv_id:      (存在するもののみ)
tags: [t1, t2]
reading_status: reading
created: / updated:           (ISO8601)
---

# <タイトル>

> **一言要約**: ...

## <note_templateのセクションラベル>      (項目のあるセクションのみ)

- **<タイトル>** — <内容(markdown)>
  - _出所: AI回答 / 種別: AI解釈 / 検証: verified_   ← 検証はunverified以外のみ
  - _原文: p.5 | 3 Method > 3.2 Loss | “quote先頭80字…”_  ← アンカーがある場合

## 質問と回答の履歴

### Q: <質問文>
- _原文: ..._
> <選択箇所冒頭300字>

**A** _(AI回答 / <model>)_:

<回答markdown>
```

規約:
- 出所・種別ラベルは `app/export.py` の ORIGIN_LABEL / INFO_LABEL が正本(§13 の区別を外部でも保持)。
- テンプレート外セクションは末尾に section_key 見出しで出力(データを落とさない)。
- 空セクションは出力しない。

## JSON

- `GET /api/sources/{id}/export.json` — source / versions / blocks / questions / answers / knowledge_items / highlights / translations / tags の全レコード(DBスキーマそのまま。再インポートや他ツール取込を想定した無損失ダンプ)。
- `GET /api/export/all.json` — `{"exported_at", "sources": [<上記>...]}` 全資料。

## 可搬性(§19)の担保

| データ | 退避手段 |
|--------|---------|
| 原本PDF/HTMLスナップショット | `data/files/` をそのままコピー |
| 全構造化データ | all.json |
| 人間可読ノート | export.md(資料ごと) |
| DB自体 | `data/knowledge.db`(SQLite標準形式) |
