# Import確認UI / Paper Brief表示

更新: 2026-10-04。T2A-04（確認UI）/ T2-05。
基点: main `809639c71af1650b568a67ec39aa7e7688029296`（PR #3）。

## 実装範囲

FastAPI / SQLite / build-free Vanilla JSを維持し、既存のvalidate / preview / commitと
Paper Brief取得APIを使用する。DB schema・migration・Importアルゴリズムの変更はない。
ブラウザはZIPを展開せず、照合・根拠解決・status降格・競合保護を複製しない。

LibraryとReaderの主要操作に「ChatGPTから取り込む」を追加した。
ReaderはPaper Brief / 概要 / 対話 / ノートの4タブ。初訪問はPaper Brief、再訪は既存の
activeTab設定を尊重する。Import完了から開く場合はPaper Briefを優先する。

## UI flow

1. **Package選択**: knowledge_growth用 `.kgpack` ファイル選択。一般ZIP/PDFとして扱わない。
2. **Validate**: 既存validate APIで検証し、preview APIでstagingを作成。
3. **Source matching**: packageのtitle/authors/year/DOI/arXiv/hash、matched/ambiguous/unmatched、
   strength/method、local candidateを表示。一意のstrong matchだけが自動target。
   weak/ambiguous/unmatchedは候補または既存PDF一覧から明示選択する。PDF未登録なら先に登録するよう案内。
4. **Import Preview**: field名・incoming/current値・status・origin・action・Evidenceを表示。
   add/update/preserve_user/excludedはbackend結果を使う。checkboxによるfield除外、対象変更、
   preview再生成はすべてfresh preview APIを呼び、処理中は操作を無効にする。
5. **User confirmation**: 対象論文、Import/除外/ユーザー保持field数、unresolved（page_only含む）/
   ambiguous Evidence数を表示。詳細を再確認できる。warning/conflictは確認画面でも表示する。
6. **Commit**: 明示的な「取り込む」クリックでのみconfirmed=trueを送る。
7. **完了**: 保存field数とverificationがunverifiedのままであることを表示し、Paper Briefを開く。

native dialogを既存modalの外観で表示する。キーボードfocusをdialog内に保ち、Escapeで閉じる。
リクエスト処理中は閉じる操作も無効。モバイル幅ではfield差分を縦に並べる。

## Paper Brief

- 30秒Brief: 一言要約、研究目的、背景、問題、対象タスク、新規性、Key Results。
- モデル・学習・評価: model family/base model、learning regimes、shots、adaptation methods、datasets、metrics。
- Structured Brief: CoreとAI拡張の全field。折りたたみ内でも未登録fieldを省略しない。
- confirmed/derived/uncertain/not_reported/not_applicableを文字と穏やかな色で区別する。
  statusとverificationは別概念。origin・ユーザー編集・package/generator/import日時を確認できる。
- 省略された任意fieldは「未登録」、null/空配列は値なしとしてstatusと併記する。
  未登録をnot_reportedやnot_applicableへ推測で変換しない。
- Key Resultはdataset/task/setting/metric/score/unit/split/comparisonと個別status/Evidenceをセットで表示。
- 原文PDF CTA。日本語版CTAは「今後対応」として無効。PDF以外ではPDF CTAも無効。
- 再読込では保存済みBriefをGETする。APIキーや実AI呼出しは不要。

## Evidence navigation

resolved / candidates / page_only / unresolvedを明示する。
resolvedは「📍 原文を見る」でPDFへ切り替え、Phase 1 `resolveAnchor`と
`pdfviewer.scrollToEvidence`を使用して現行document_blocks上のbboxへ戻る。
PDF読み込み完了を待ち、Readerが切り替わった場合の遅延navigationを破棄する。
古いAnchorが解決できなければPhase 1の候補/ページ/未解決fallbackを表示し、黙って位置を選ばない。

Import Previewからの根拠確認は別タブのReaderを開くため、wizardの選択やpreviewを保持する。
Readerのhashに閲覧用Anchorを渡す。読取とnavigationのみで、保存APIは呼ばない。
候補は全文とページを一覧表示し、閲覧先に「候補の閲覧中・根拠未確定」を表示する。
候補のbboxマーカーは破線。保存済みEvidenceやBrief revisionは変更しない。
page_onlyはページ移動のみで「根拠未確定」、以前のbboxマーカーも消す。
unresolvedは原文位置へのボタンを出さない。可搬参照のquote/page/heading/labelは確認できる。

## conflict / error UX

- ユーザーorigin / user_edited fieldは「上書き不可」。preserve_userは既存値を保持する。
- existing field update、status降格、ambiguous/unresolved Evidenceをwarningとfield内に表示する。
- 任意Note/Q&Aは内容を確認できるが、ノート・対話には未反映。package履歴への保存のみ。
- duplicate packageはwarningとして表示し、確認へ進めない。
- 対象/除外変更後のpreview取得失敗では古い内容を表示したまま、未反映を明示し、確認/commitを無効にする。
- commit失敗ではpreview・除外・対象を保持する。一般の失敗は明示retry可能。
- 409（stale/期限切れ/duplicateなど）はcommitを無効にし、preview再生成と再確認へ戻す。
  再生成した内容を自動commitしない。
- package/field/quote文字列はDOM textとして表示する。HTML/Markdownとして実行しない。

## 検証

すべてgenerated PDF / kgpackと一時DB。実論文、実AI、APIキーは不要。

```bash
.venv/bin/python -m pytest tests/ -q
# Optional browser test dependencies (app/build dependenciesには追加しない)
.venv/bin/pip install playwright
.venv/bin/python scripts/smoke_import_brief_browser.py --chromium /usr/bin/chromium
```

browser scriptは一時ディレクトリのmockサーバーを起動し、終了時にDBを削除する。
Chromiumのスクリーンショットは既定で `/tmp/kg-import-brief-browser/` に保存する。
検証結果（2026-10-04）:

- pytest: **150 passed, 1 warning**。既存Starlette/httpx deprecation warningのみ。
- Import / Brief browser smoke: **34 checks passed**, Chromium、pageerrorなし。
  除外preview取得失敗からの復旧、weak matchの明示選択、候補閲覧後のrevision不変も確認。
- 既存Phase 1 browser smoke: 成功。2段組/回転cropの選択、highlight再読込、mock Q&A、
  quote/bbox/candidates/page_only fallbackが継続して動作。
- JavaScript構文確認・git diff --check: 成功。
- スクリーンショットを目視し、Import差分・Reader Brief・PDF bbox marker・390px幅dialogを確認。

## 既知の制約 / 次のタスク

- Evidence候補の永続確定APIは未実装。候補閲覧のみ。
- Optional Note/Q&Aの既存テーブルへの反映・item選択は後続。画像/PDF/asset入りpackageはbackendが拒否する。
- Evidenceは原文位置の一致であり、主張の正しさを検証したものではない。
- OCR、複数blockにまたがる引用、fuzzy matching、preview GCなどbackendの既存制約は継続。
- 全fieldを表示するため、大きいpackageではpreviewが長くなる。Structured Briefと由来/根拠詳細は折りたためる。
- Import Previewの原文確認は別タブ。現在のReader全体の狭幅レイアウト再設計は対象外。
- AI runtime / app-server / OAuth / 実AI生成 / Japanese Reader / Compare等は未実装。
- 次はEvidence候補の明示確定・再解決のbackend契約とUI、任意Note/Q&A反映の安全な契約を推奨。
  AI runtimeはT0A、日本語版ReaderはT3の別タスクとして進める。
