# RDR-01 検証記録

2026-10-15、Linux / Python 3.12。自作素材のみを使用した。
MMADの本文/PDF/図、ユーザーDB、private資料は開いていない。アプリからの実AI呼出しは0。

## HTML / 英文preview

```sh
python -m pytest tests/test_reading_document.py -q
python scripts/demo_reading_document.py
node scripts/smoke_reading_document_browser.js
```

新規40件成功。明示版、版不明・不一致、abstract-only、HTML不存在、接続障害、robots拒否、
403/429の無再試行・後続停止、redirect/host/protocol、正常baseと不正base、
cache再利用/hash改変、offline素材欠落、破損画像、SVG非挿入、script/event/危険なURL、
表セル結合・値、MathML/TeX（LaTeXMLのequation table/alttextを含む）、Caption・参照、原文保持、重複・空領域・変換警告を確認した。

全体回帰は **370件成功**（既存330件、新規40件、121.97秒）。
最終の取得失敗時provenanceの確認を含む対象テストも40件成功（0.67秒）。
既存依存からanyio BlockingPortalのdeprecation warningが1件あり、失敗はない。
AGENTS.mdに従って実装前にbaselineを開始したが、sandbox内のTestClient起動で停止した。
同一テストはsandbox外で成功したため、全体回帰もsocketを利用できる環境で実行した。
切り分け中に専用venvをFastAPI 0.115.14 / Starlette 0.46.2へ変更した。
requirementsの既存バージョン条件は変更していない。Pillow 12.3.0、httpx 0.28.1、
PyMuPDF 1.28.2、beautifulsoup4 4.15.0を使用した。

Chromiumのネットワークをofflineにし、Playwrightのrouteでローカルファイルだけを配信した。
管理下Chromiumではfile URLの直接表示が制限されるため、この方法で確認した。
1280pxと390pxで、画像1/1ロード、結合セル2、MathML1、内部参照の到達、横あふれなし、
page errorなし、外部HTTP要求0を確認。画面も目視した。

- [PC英文preview](evidence/english-1280.png)
- [スマートフォン英文preview](evidence/english-390.png)
- [browser report](evidence/browser-report.json)
- [合成英文HTML](example/english_preview.html)

合成fixtureの独立blockは15件（title/metadata/abstract/heading/paragraph/list/figure/table/
equation/footnote/reference）。画像1、図1、評価表1、数式1、素材未解決0。
元DOMのparagraphは5件、独立paragraph blockは3件で、Abstract内の2段落はAbstract block内にある。
表・数式の候補数には外側containerと内側table/mathも含まれるため、独立blockとは分けて報告する。
抽出器の出力だけを分母にする完全性評価は行っていない。
mockのID `2601.00001v2` はテスト用識別子で、実arXiv論文を取得した証拠ではない。

## PDF-only 独立runner

```sh
python scripts/generate_pdf_fixtures.py --output-dir reading-output/pdf-fixtures
python scripts/compare_reading_pdf.py reading-output/pdf-fixtures/two_column.pdf reading-output/pdf-fixtures/visual_evidence.pdf --output reading-output/pdf-comparison
```

DoclingとPyMuPDF4LLMへ同一の2資料を渡すrunnerを追加した。
両者はアプリ必須依存にしない。Doclingは未導入で、ローカルmodel artifactsも未準備のため未実測。
runnerはDoclingのmodel directoryを明示しない限り変換を始めない。OCR・remote serviceを無効化し、
model downloadもoffline設定で抑止する。ツールを確定するための両者比較は未完了。

PyMuPDF4LLM 0.0.17を専用venvへ追加し、既存PyMuPDF 1.28.2で実行した。
軽量な既存Markdown経路の調査に使用した版であり、最新版の評価や採用決定ではない。

| 資料 | 時間 | 抜き取り確認 |
|---|---:|---|
| two_column.pdf | 0.0908秒 | LEFTの1〜8、RIGHTの1〜8、2ページ目の順。対象段落の欠落は見つからなかった |
| visual_evidence.pdf | 0.0277秒 | Caption/数式文字/12行の表値は出力。図中のOutput文字が欠落し、図形も未保存。表はセル構造を持たないテキストへ変換された |

時間は提出用reportを生成した各1回の値。warm/cold条件の比較や一般化はしていない。
図中のOutputは元のgenerated PDFに存在することをPyMuPDFの原文抽出で確認した。
MarkdownではInput/Encoderがheadingへ寄り、図のCaptionも見出し扱いになった。
この試験では画像markerは0。図とCaptionの構造的な結び付けは未解決。
これらはrunnerの観察であり、既存アプリのPDF処理を変更する根拠とはしていない。

[比較report](pdf_comparison/comparison_report.json)と、自作素材だけの
[2段組出力](pdf_comparison/0-pymupdf4llm.md)・[図表出力](pdf_comparison/1-pymupdf4llm.md)を保存した。
Windows導入負担は両者とも未計測。Doclingの依存・model準備、PyMuPDF4LLMのwheel導入と
実行をWindowsで確認してから比較を完了する。現時点のselected_engineはnull。

## 未検証・制約

- 実arXivへのHTTP成功、実際のrobots/利用条件変更、MMAD全DOM・全素材の取得は未確認。Cloudでは指示に従い実データを取得していない。
- 同版URL・文書宣言は照合するが、登録PDFのID/v番号や内容・図表の一致は未確認。参考文献から同版判定しない。
- arXiv HTML自体の欠落や変換誤りを、HTML単独では検出し切れない。全文100%とは報告しない。
- SVG/canvas/object等は直接埋め込まず、未対応表示と原PDF確認へ案内する。自動PDF領域fallback・完全bbox同期は後続。
- CSSを除去した自前レイアウトのため、複雑なMathMLや表の見た目は実資料で要確認。TeXだけの数式を外部JSで組版する機能はない。
- Docling実測、Windows、ユーザー管理下のMMADと同版PDFの抜き取り照合が残る。
- 実AI翻訳、日本語PDF出力、DB統合、AIRuntime接続、既存Readerの改造は今回実行していない。
