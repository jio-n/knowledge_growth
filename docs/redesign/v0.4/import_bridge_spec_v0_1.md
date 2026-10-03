# ChatGPT Import Bridge 仕様 v0.1

最終更新: 2026-10-04
対象: knowledge_growth MVP v0.4

## 1. 目的

ChatGPT上で論文を解析し、Paper Brief・Evidence・Knowledge候補・Q&A候補を構造化成果物として出力し、ローカルのknowledge_growthへ安全に取り込む。

このBridgeは、API従量課金を使わずにChatGPT側の高度な読解結果をアプリへ持ち込むためのMVP機能である。

## 2. 基本原則

1. PDF原本は原則 `.kgpack` に含めない。
2. ローカルPDFを正本とする。
3. Import前に必ずPreviewする。
4. Evidenceは可搬参照からローカルAnchorへ再解決する。
5. 未解決Evidenceは未解決のまま表示する。
6. ユーザー編集を自動上書きしない。
7. ImportしたAI生成物を原文事実と混同しない。

## 3. パッケージ形式

backendの厳密なwire契約・APIは
[Import foundation](paper_brief_import_foundation.md)を参照。
manifest version keyは`kgpack_schema_version`、`payloads`を必須とする。
PDF/assetはこのbackend versionでは常に拒否する。

`.kgpack` はZIP互換コンテナ。

```text
example.kgpack
├ manifest.json
├ paper_brief.json
├ evidence_refs.json
├ knowledge_items.json      # optional
├ qa_threads.json           # optional
└ assets/                   # optional
```

### manifest.json

```json
{
  "kgpack_schema_version": "kgpack-0.1",
  "package_id": "...",
  "source_identity": {
    "title": "...",
    "authors": ["..."],
    "year": 2026,
    "doi": null,
    "arxiv_id": null,
    "source_hash": null
  },
  "generated_by": "ChatGPT",
  "generated_at": "...",
  "paper_brief_schema_version": "paper-brief-0.1",
  "provenance_notice": "AI-generated analysis; verify against source",
  "payloads": ["paper_brief.json", "evidence_refs.json"]
}
```

## 4. Evidence参照

ChatGPT側ではローカルblock_idやbboxを前提にしない。

```json
{
  "evidence_id": "ev-001",
  "page": 4,
  "heading": "3.2 Method",
  "quote": "...",
  "prefix": "...",
  "suffix": "...",
  "figure_label": "Figure 2",
  "table_label": null,
  "status": "portable"
}
```

Import時にローカルのSourceAnchorへ解決する。

## 5. Source Matching

優先順:
1. source_hash
2. DOI / arXiv ID
3. title + authors + year
4. user selection

mismatch時は自動commitしない。

## 6. Import Preview

Preview画面には以下を表示:
- package sourceとlocal sourceの照合結果
- Paper Brief追加/変更field
- Knowledge Item / Q&A候補
- Evidence resolved / ambiguous / unresolved件数
- 既存user-edited fieldとの競合
- Import除外チェック

## 7. Commit Policy

- 1 transactionで反映
- 失敗時rollback
- `origin=chatgpt_import` 等を付与
- package_id / generated_by / imported_at / schema_version保持
- user originを自動上書きしない
- verificationを自動でverifiedにしない

## 8. PDF・画像の扱い

MVPのkgpackには原則PDF原本を含めない。
Figure/Table画像も原則ローカルPDFからknowledge_growth側で再生成する。
ChatGPT側で画像を含める場合は、ユーザー自身が作成したもの、再配布可能なもの、または権利上問題ないものに限定する。

## 9. UI

Library / Readerに以下を追加:

```text
[ + PDF ] [ URL ] [ ChatGPTから取り込む ]
```

Import後も通常のPaper Brief / Note / Search / Evidence UIへ統合する。

## 10. 将来拡張

- knowledge_growth MCPによる直接書き込み
- ChatGPT/Codexから `save_paper_brief` / `save_note` / `save_visual_clip_request`
- direct sync

ただしMVPはファイルImportを正本とし、直接接続を必須依存にしない。