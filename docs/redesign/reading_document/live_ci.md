# RDR-01: 公開MMAD HTMLの一回検証

ユーザーの2026-10-15の明示承認により、公開MMAD `2410.09453v3` の取得・英文preview生成・検査をGitHub Actionsで実行する。これは従来の「実MMADはローカルのみ」という制約に対する限定的な追加承認である。ユーザーの登録PDF、SQLite、原文研究資料、認証情報にはアクセスしない。PR #9とmainは変更・mergeしない。実AI翻訳、日本語PDF、DB統合は今回も対象外。

## 範囲

- PR #10、同一repositoryの `spike/reading-document-html-first` に限定する。
- 新しい検証workflow/script/testの変更時のみ実行。定期実行は設けない。
- `2410.09453v3` 一論文だけを現行snapshotで取得。robots.txt、crawl-delay、redirect/host検証、403/429停止、各容量上限を維持する。アクセス拒否を回避しない。
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

## 実行

```sh
python scripts/check_rdr01_live.py --scratch /tmp/rdr01-source --report-dir /tmp/rdr01-reports --browser
```

既存appは起動しない。必要な依存は現在のrequirementsとPlaywright Chromium。

```sh
python -m pytest tests/test_rdr01_live_check.py -q
```

追加12ケースは自作HTMLのみ。DOM差、セル結合、画像/数式欠落、参照切れ、版/hash/順序、不完全なoffline再利用、取得拒否、出力への本文漏えい防止を確認する。ローカル単独実行は12 passed。全体テストとlive取得の結果は実際のActions実行を確認してから追記する。

参照:
- https://info.arxiv.org/help/robots.html
- https://arxiv.org/robots.txt
- https://arxiv.org/abs/2410.09453v3
- https://arxiv.org/licenses/nonexclusive-distrib/1.0/
