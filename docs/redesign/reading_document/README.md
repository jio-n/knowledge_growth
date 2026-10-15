# ReadingDocument / RDR-01

2026-10-15。正本は[PR #9の方針変更コメント](https://github.com/jio-n/knowledge_growth/pull/9#issuecomment-6183569211)。
コメントを末尾の **END OF TASK RDR-01** まで取得・確認した。
作業開始時の最新mainは `a93556be1218c717c10470d78ab803c1f24e9db2`。
独立worktreeと `spike/reading-document-html-first` を使用し、PR #9の変更は取り込んでいない。

## 方針と仕様差分

原PDFを保持し、構造付き英文→全文和訳と原図表→HTML読書ビュー→任意PDF保存へ進む。
arXivの同版HTMLを優先する。本文は内容・読み順・数値・引用・識別子を検査し、
通常本文の翻訳開始条件にfont/glyph/pixelの一致を求めない。
この決定は、旧PR #9の局所的なPDF再現修正指示とv0.4旧本文の描画再現要件より優先する。

| 項目 | 新しい扱い |
|---|---|
| 本文 | 原章立て・段落・リスト・付録・文献を保つ。要約や再編はしない |
| 図表・数式 | 原画像、表セルと結合、MathML/TeX、Caption/labelと参照を保持 |
| 同版 | 明示的なIDとv番号を固定。別版・版不明を代用しない |
| 根拠 | HTMLはDOM IDとDOM path。PDFはpage/必要ならbbox。HTML用の偽PDF block IDは作らない |
| 未翻訳 | 将来は段落単位で原文を表示。文中の小さい英語cropへ戻らない |
| 閲覧・保存 | 原PDF／日本語文書／英日対訳が目標。日本語PDFの生成成否と閲覧を分離 |
| PDF-only | 既存解析器を独立比較。巨大な自作PDF解析器の追加調整へ戻らない |

今回実装したのはRDR-01の独立CLIだけ。既存アプリのReader、AIRuntime、SQLite、migration、
SourceAnchor、既存訳、ユーザー編集、Note/Brief/Highlightへ接続しない。
実AI呼出しは0。日本語翻訳・日本語PDF生成・全面Reader改造は未実装。

## 使い方

既存アプリを起動せず、リポジトリの作業フォルダで依存を導入したPythonから実行する。
出力先には専用フォルダを指定する。`reading-output/` はGitから除外済み。

```sh
python -m app.reading_document 2410.09453v3 --output reading-output/mmad
```

Windowsでは `python` を `.venv\Scripts\python.exe` に置き換えられる。
一回のコマンドで公式HTML、使用画像、構造化文書、英文preview、診断を保存する。
入力は版付きID、または公式のabs/html/pdf URL。登録PDFや参考文献からIDを自動抽出しない。

```text
reading-output/mmad/2410.09453v3/
  reading_document.json
  english_preview.html
  acquisition_report.json
  assets/<sha256>.<extension>
  source.html.txt
  snapshot_manifest.json
```

`english_preview.html` を開くと、保存した画像・本文・表・数式を読める。
ブラウザがfile URLを許可しない場合は、出力先だけをローカル静的サーバーで配信する。
既存アプリやDBを起動する必要はない。未取得素材は個別の欠落表示と原PDF確認案内になる。
同じ出力先で再実行するとhash検証済みcacheを使う。

```sh
python -m app.reading_document 2410.09453v3 --output reading-output/mmad --offline
```

`--offline` はネットワーク要求を行わない。素材cacheの欠損も再取得せず報告する。
source hash不一致は失敗とする。再取得が必要な場合は新しい専用出力先を指定する。
終了コード0は取得処理の成功を表し、全文完全性や同版PDFとの一致を保証しない。
コード1でも `conversion_incomplete` なら欠落を表示したpreviewがある。
版違い・全文HTMLなし等の失敗では、誤ったpreviewを残さない。入力不備はコード2で標準出力へ診断する。

## ユーザー管理下のMMAD確認を一回にまとめる手順

CloudではMMAD本文・PDF・図を取得していない。実データ確認は次をローカルで一度行う。

1. 登録PDFの表題・著者・冒頭のarXiv表記等で `2410.09453v3` を確認する。参考文献のIDを使わない。版不明なら同版確認を保留する。
2. 上記CLIで取得する。PDFの版を確認できた場合だけ `--registered-pdf-id 2410.09453v3` を追加できる。この引数は利用者の明示情報で、PDFの自動照合ではない。
3. 英文previewでtitle、Abstract全文、Introduction、図とCaption、評価表の値・セル結合、付録・文献をまとめて読む。reportの素材欠落・参照切れ・重複・空領域・変換警告を確認する。
4. 同じローカルPDFと、Introductionの段落、代表図の内容・Caption、評価表の数値、付録・文献を抜き取り照合する。相違箇所と未確認項目をローカルの `local_review.md` に記録する。
5. ネットワークを切り、previewと保存素材が読めるか確認する。必要なら上記offlineコマンドで再生成する。

private PDFのuploadや本文のagent共有は不要。本文や画像を含むsnapshot/report一式をGitへ追加しない。
開発側へ共有する場合は、実本文を含まない状態・件数・未確認項目だけにする。
実AI翻訳は、RDR-01の受入後に対象範囲と利用枠消費を確認してから扱う。

## 保存契約と安全性

`ReadingDocument` schema 1 / parser `rdr-html-1`。
sourceにはrequested/resolved URL、ID/v番号、取得日時、表現種別、SHA-256、版の根拠を保存する。
metadata、DOM順のblocks、素材一覧、サニタイズ済みrich HTMLを持つ。
blockはkind/order/source_ref/ancestor DOM IDs/original_text/html/asset_refs/captions/labels/referencesを持つ。
表は行・セル・rowspan/colspan・原値、数式は安全化したMathMLとTeX annotationも記録する。
sourceの `registered_pdf_match=not_checked` は、明示引数を与えても自動で確定へ変えない。

取得先は `https://arxiv.org` のみ。redirect先と画像URLも要求前に検証する。
baseは同じID/v番号のHTML配下だけ採用する。bibliographyのIDで文書を識別しない。
robots.txtを先に確認し、取得不能時は先へ進まない。標準間隔は3秒、crawl-delayがあれば延ばす。
403/429は再試行せず、素材の403/429等では後続素材要求も止める。全文HTMLは20MB、画像は15MB、
総取得量は150MB、画像は200 URL、redirectは上限付き。無差別crawlや制限回避を行わない。
利用前に[arXivの自動取得方針](https://info.arxiv.org/help/robots.html)と
[experimental HTMLの説明](https://info.arxiv.org/about/accessible_HTML.html)も確認する。

原ページは自前ビューへ無加工挿入しない。タグ・属性をallowlistから再構成し、script、event handler、
style、iframe、危険なリンク等を除去する。画像はMIME/signature/寸法/復号を検査し、hash名で保存する。
SVG等の未対応素材は欠落として案内する。外部JS/CSS/font依存はない。CSPも付与する。
raw snapshotは `.txt` に保存し、previewへ直接挿入しない。

取得状態は `version_unknown` / `version_mismatch` / `html_unavailable` / `temporary_failure` /
`forbidden` / `rate_limited` / `conversion_missing` / `conversion_incomplete` 等を分ける。
reportは元DOMのparagraph/image/table/math件数と、block inventory、重複、空領域、参照切れ、
素材ごとの成功・欠落、除去markup、HTML変換警告を記録する。元DOM件数とblock件数は、
Abstractやfigure等を一単位にするため一致しない。いずれもPDF由来の全文coverageの分母にはしない。

## 検証・残課題

[検証結果とPDF比較](validation.md)、[合成英文preview](example/english_preview.html)、
[取得report](example/acquisition_report.json)、[ReadingDocument](example/reading_document.json)を参照。
自作fixtureの成功はMMADでの成功を意味しない。

次はユーザー管理下の同版照合結果を受けてRDR-01をレビューし、その後に既存AIRuntimeを用いる
全文翻訳・日英Readerを別タスクとして設計する。DB統合や翻訳を今回のCLIから起動しない。
