# Generated PDF fixtures

外部の論文、画像、フォントを使わず、PyMuPDFの標準フォントとベクター描画で生成する。
本文・著者名・モデル・スコアはすべてテスト用の架空データ。実際の研究成果として扱わない。

| 名前 | 内容 | 用途 |
|---|---|---|
| `simple` | 見出し・本文・メタデータを持つ2ページ | PDF登録、質問、翻訳、ノート、export、再訪 |
| `two_column` | 右段を先に描画した2段組、LEFT/RIGHTの識別文 | Phase 1で座標に基づく読み順を検証する下地 |
| `visual_evidence` | Architecture図、caption、本文参照、13行の表、数式、両ページで重複する引用 | 後続Phaseの図表・数式・曖昧な根拠参照の検証用 |

pytestでは `pdf_factory.make_pdf()` からメモリ上に生成し、バイナリをGitへ追加しない。
固定メタデータと `no_new_id=True` を使い、同一PyMuPDFバージョンで同じバイト列を生成する。
異なるPyMuPDFバージョン間のバイト一致は保証しない。

ブラウザ確認用にファイルを生成するには、リポジトリルートで実行する。

```bash
.venv/bin/python scripts/generate_pdf_fixtures.py
# 出力先を変更する場合
.venv/bin/python scripts/generate_pdf_fixtures.py --output-dir /tmp/kg-pdf-fixtures
```

既定出力先 `tests/fixtures/generated/` はgitignore対象。Windowsでは
`.venv/bin/python` を `.venv\Scripts\python.exe` に読み替える。

Phase 0のテストは生成可能性、抽出本文、見出し、ページ番号を確認する。
2段組の正しい読み順、bbox、図表の構造化抽出・cropを実装済みとは扱わない。
