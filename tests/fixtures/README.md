# Generated PDF fixtures

外部の論文、画像、フォントを使わず、PyMuPDFの標準フォントとベクター描画で生成する。
本文・著者名・モデル・スコアはすべてテスト用の架空データ。実際の研究成果として扱わない。

| 名前 | 内容 | 用途 |
|---|---|---|
| `simple` | 見出し・本文・メタデータを持つ2ページ | PDF登録、質問、翻訳、ノート、export、再訪 |
| `two_column` | 右段を先に描画した2段組、LEFT/RIGHTの識別文 | 座標に基づく左列→右列の読み順を検証 |
| `visual_evidence` | Architecture図、caption、本文参照、13行の表、数式、両ページで重複する引用 | 後続Phaseの図表・数式・曖昧な根拠参照の検証用 |

| `column_bands` | 2段組の途中に全幅見出し | 帯ごとの左→右の読み順 |
| `duplicate_evidence` | 前後文脈が違う重複引用 | 同点候補・文脈fallback |
| `image_evidence` | PyMuPDFで生成した単色ラスタ画像とcaption | 埋め込み画像領域のbbox |

make_pdfのshift / rotation / crop引数で、本文移動・90度回転・CropBoxも生成する。
Node.js 18+をPhase 1のresolverテストに使用する（アプリ起動・buildには不要）。
実際のclient/js/anchor.jsを実行し、Pythonでアルゴリズムを複製しない。

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

Phase 1では読み順・block/span bbox・図表/数式の候補・安定ID・Anchor fallbackと
曖昧性・新versionの再抽出・旧schemaからのデータ保全を確認する。
図表の意味解析、asset保存、crop生成、Visual Clip UIは未実装。

## generated .kgpack

`kgpack_factory.py`は15種類の完全な自作packageをメモリ内で生成する。
`visual_evidence` PDFの架空スコア/文を使用し、実論文や実AIは利用しない。
`scripts/generate_kgpack_fixtures.py`は/tmp等へPDF/package/template/JSON schemaを生成する。
user conflict / duplicate importは同一packageと対応する生成DB状態で検証する。
