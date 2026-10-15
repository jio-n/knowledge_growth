# RDR-01: 公開MMAD HTMLの一回検証


## 最新追試（2026-10-16 JSTに向けた検査）

PR #10の変更を加えた状態で、実際の公開MMAD v3 HTMLを再取得し、runner内だけで監査した。登録PDFとの照合・AI翻訳は未実施。

| GitHub Actions | commit | 全pytest | 実MMAD監査 |
|---|---|---|---|
| [run #4](https://github.com/jio-n/knowledge_growth/actions/runs/38770445639) | `65c5085` | 402 passed | 本文45/45一致、全画像15/15取得・表示、幅390pxで横あふれなし、未対応markupで要確認 |
| [run #5](https://github.com/jio-n/knowledge_growth/actions/runs/38772223070) | `e5f70fc` | 404 passed | 45段落・全見出し・25キャプション・922表セルが順序と文字列一致、画像15/15、browser正常。依然要確認 |

最新監査の「不完全」は通常本文ではなく、次の残存事項である。いずれも安全確認なしに単純な成功扱いへ変更しない。

- **科学的な図表の欠落リスク**: `<object type=image/svg+xml>` がFigure内に1件あり、relative sourceを参照し、埋め込みfallback画像もfallback文字もない。現在は未対応・原PDF確認の案内を表示している。これは図表の中身が失われる可能性があり、RDR-01の受入前に原物表示／ローカルPDF fallbackを要検討。
- **メールリンク3件**: `mailto:` hrefを安全方針でリンクとしては除去している（表示テキストは保持）。外部HTTPなどを無条件許可しない。
- **空領域1件**: 表（table）の空要素候補。表セル922件の文字列・順序と結合状態は元HTMLと一致。現時点でこの空要素が意味を持つかは未判定。
- 前回69件の `cite` は安全なインライン表現として保持し、追加検査では除去0件となった。
- Abstractは原HTMLとPreviewの内容が一致。MathMLタグは当該原HTMLに0件で、数式一般の正確さは別検証。

rdr-live-2の安全診断から、元HTMLの画像15、Figure25、Table18、参照切れ0、warning0、p45/45一致、オフライン再利用・画像15/15表示・390/1280px横あふれ0を確認。GitHub Actionsのworkflow全体が failure なのは、`conversion_incomplete` を意図的にfail-closedしているためで、通信失敗ではない。

前回の個別不具合（段落の文字ノード境界・スマートフォン横あふれ）は解消した。**現在の最大課題は未対応SVG図表1件の閲覧であり、次の判断ではテキスト抽出処理の小修正を繰り返さず、科学的素材の保持／原PDF fallbackを検討する。**

この追試のpublic artifactは数値・状態・安全な分類のみ。原HTML、本文、実図表、preview画像、登録PDF、認証情報は含めていない。



ユーザーの2026-10-15の明示承認により、公開MMAD `2410.09453v3` の取得・英文preview生成・検査をGitHub Actionsで実行した。従来の「実MMADはローカルのみ」という制約に対する限定的な追加承認である。ユーザーの登録PDF、SQLite、原文研究資料、認証情報にはアクセスしていない。PR #9とmainは変更・mergeしていない。実AI翻訳、日本語PDF、DB統合は今回も対象外。

## 実行結果（2026-10-15）

- 実行: https://github.com/jio-n/knowledge_growth/actions/runs/38714878065
- 検査したcommit: `b67e8f626c637b8a3dbbe5f6e63ab5df0a42b95a`
- Ubuntu / Python 3.12.15、全体pytest **382 passed, 1 warning in 130.07s**（既存370＋追加12）。開発者の報告の再掲ではなく、このActionsログで確認した結果。
- 実arXiv HTMLを取得し、英文previewを生成。HTML内で参照する15画像をすべて取得し、hashを確認した。
- 元HTMLとpreviewで、h1 1件／h2 8件／h3 17件、li 70件、figcaption 25件、td 922件の文字列と順序が一致。表セルのrowspan/colspanも一致。
- 段落pは45件→45件だが、全体の文字列・順序比較は不一致。差のある段落数と具体的な差分の種類は現在のsafe reportからは未特定。意味のある欠落か、除去markupや表示用注記の影響かを断定しない。
- Abstractのテキストは元HTMLとpreviewで一致。今回の正規化後1,340文字にはHTML見出し・空白の扱いも含むため、以前のPDF由来1,150文字と直接比較しない。日本語化の成功ではない。
- 元HTML/previewのimgは15/15、figure要素25/25、table要素18/18。DOM要素数であり、論文内の独立図表数と同一とは限らない。
- MathML要素は元HTMLとpreviewの双方0件。この実データrunだけで数式の完全保持を検証したとは言わない。
- 内部リンク切れ0、取得warning0、参照未解決0。版付きHTML識別とsource hashは一致。登録済みPDFとの照合は **not_checked**。
- offline再構築はcache再利用、ネットワーク要求0。Chromiumでも1280px/390pxとも画像15/15表示、pageerror0、外部要求0。
- 1280pxでは横あふれなし。390pxでは横あふれあり。
- 取得器の状態は **conversion_incomplete**。監査結果は **needs_review**。終了コード1によりworkflow全体は **failure** のまま。成功表示や検査の緩和はしていない。

safe artifact: https://github.com/jio-n/knowledge_growth/actions/runs/38714878065/artifacts/11962647919

アーティファクトはlive_audit.jsonとsummary.mdの2ファイルだけ。実際に取り出して内容を確認した。原HTML、本文、図表、preview、スクリーンショットは含まない。

初回のworkflow定義ではrunner context位置が不正だったため修正した。次の試行ではno_aiが既存mock fixtureを上書きしたため、pytest stepでKG_AI_RUNTIMEをunsetするよう修正した。これら2試行は実HTML取得stepに到達していない。上記runが実MMAD取得・検査に到達した実行である。

この実行後の変更は再実行の起動条件と本記録のみ。取得・解析・監査のPythonコードは実行commitから変更していない。通常のpushが意図せず実取得を繰り返さないよう、以後は明示labelで起動する。

## 範囲と再実行

- PR #10、同一repositoryの `spike/reading-document-html-first` に限定する。
- 再実行はPRに **rdr01-live-check** ラベルを追加する操作で明示的に開始する。既に付いている場合は外してから追加する。通常のpush・定期処理では実arXivへアクセスしない。
- このlabel起動への変更後は、不要な再取得を避けるためlabelを付けて実行していない。取得・監査本体の実行結果は上記runである。
- `2410.09453v3` 一論文だけを現行snapshotで取得。robots.txt、crawl-delay、redirect/host検証、403/429停止、容量上限を維持する。アクセス拒否を回避しない。
- freshなrunner一時領域へHTMLと素材を取得し、同じsnapshotをofflineで再構築する。
- 元DOMとpreviewの見出し・段落・Caption・リスト・表セルの文字列/順序、rowspan/colspan、図・表・画像・MathML件数を比較する。画像hashと内部リンク、明示版を確認する。
- Chromiumをofflineにし、1280/390pxでローカルpreviewの画像ロード・横あふれ・pageerrorを検査する。実ネットワークへのbrowser要求はブロックする。

## 公開物

本repositoryはpublic。MMAD v3のarXiv上のlicense表示は非独占配布許諾であり、一般的な再配布可能licenseとみなさない。

- 原HTML、本文、図表、raw acquisition report、preview、スクリーンショットをGitや公開artifactに追加しない。
- 全内容はrunnerのscratch領域のみで処理する。
- artifactは `live_audit.json` と `summary.md` のみ。数値・固定コード・一致可否を記録し、本文や任意の例外文字列を出さない。保存期間7日。
- `contents: read`、secret不要、checkout credentialを保持しない。`pull_request_target`、main更新、workflowからのcommitを使用しない。
- runner一時領域は終了時に削除する。

## 判定

- `structural_checks_passed`: 取得HTMLに対する構造・内容保持検査に通過。原PDFとの完全一致や翻訳成功を意味しない。
- `needs_review`: previewはあるが、一部の構造・素材・表示に確認事項がある。
- `blocked`: 通信、robots、版不一致等で検査を完了できない。

終了コード0/1/2がそれぞれに対応する。失敗を成功表示せず、失敗時も安全なreportを保存する。途中の環境セットアップ失敗やタイムアウトではreport未生成になり得るため、workflowログも確認する。

## ローカルCLI（必要な場合のみ）

```sh
python scripts/check_rdr01_live.py --scratch /tmp/rdr01-source --report-dir /tmp/rdr01-reports --browser
```

既存appは起動しない。必要な依存は現在のrequirementsとPlaywright Chromium。

```sh
python -m pytest tests/test_rdr01_live_check.py -q
```

追加12ケースは自作HTMLのみ。DOM差、セル結合、画像/数式欠落、参照切れ、版/hash/順序、不完全なoffline再利用、取得拒否、出力への本文漏えい防止を確認する。ローカル単独12件と上記Actionsの全体382件が成功した。

## 次の判断

実データの取得経路は動作した。追試で段落差分と390px横あふれを解消したが、Figure内のSVG1件は未表示のためRDR-01受入を確定しない。HTMLの取得成功を論文全文や原PDFの完全保持と同一視しない。従来のglyph/crop処理へ戻す理由はない。

実AI翻訳、登録済みPDFとの同版・内容照合、Windows、Docling比較は今回未実施。PR #9/#10はDraft・未mergeのまま。

参照:
- https://info.arxiv.org/help/robots.html
- https://arxiv.org/robots.txt
- https://arxiv.org/abs/2410.09453v3
- https://arxiv.org/licenses/nonexclusive-distrib/1.0/
