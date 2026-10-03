# Phase 0 baseline / 検証記録

更新: 2026-10-04 / 対象baseline commit: `7c09ac3`

今回の実装は **T0-01、T0-02、generated PDF fixtures** に限定する。
Phase 0AのAIRuntime / Codex接続、Phase 1以降のReader変更は実装していない。
July 2026 MVPのFastAPI + SQLite + build-free Vanilla JSを維持する。

## 変更前の確認

`docs/handoff/current_status.md` とコードを確認した。
現行はPDF/URL/テキスト登録、PDF.js表示、mock質問、翻訳キャッシュ、knowledge_itemsの
理解ノート、SourceAnchor、Markdown/JSON export、localStorageによる表示状態の復元を持つ。
Paper Brief、bbox、ConversationThread、Visual Clip、Import Bridgeはまだない。

環境のシステムPythonにはpytestがなかったため `bash scripts/setup_codex_cloud.sh` を実行。
依存関係とvendorの準備後、変更前の既存4件は **3 passed / 1 failed**。
`test_full_workflow` が登録直後の `analysis_status == 'pending'` を要求していたが、
背景のmock解析が先に終わって `done` を返した。製品側の非同期処理は変更せず、
テストで `pending/running/done` を許容し、既存のpollで最終状態 `done` を確認する。
APIテストは共通fixtureで一時dataディレクトリとmockを使い、アプリのlifespanも実行する。

## 実装した基盤

- 現行schemaを `app/migrations/001_baseline.sql` に固定し、`schema_version` にv1として登録。
- 起動時runner、状態確認・移行CLI、既存DBのschema検証、SQLite Backup API、
  全未適用版のtransaction・foreign key検証・rollbackを追加。
- 生成PDF3種類を追加。メモリ生成を既定とし、ファイル出力も可能。
- 既存行・ID・原文アンカー・出所・検証状態・ユーザー翻訳修正の保全と、
  将来の複数版適用、SQL失敗、backup失敗、未知の版拒否を検証。

参照: [移行・復旧手順](../../development/db_migrations.md)、
[PDF fixture仕様](../../../tests/fixtures/README.md)。

## 変更ファイル（21件）

| 分類 | ファイル |
|---|---|
| DB | `app/db.py`, `app/migrations/001_baseline.sql`, `app/migrations/__init__.py`, `app/migrations/__main__.py` |
| PDF生成 | `scripts/generate_pdf_fixtures.py`, `tests/fixtures/__init__.py`, `tests/fixtures/pdf_factory.py`, `tests/fixtures/README.md`, `.gitignore` |
| テスト | `tests/conftest.py`, `tests/test_smoke.py`, `tests/test_migrations.py`, `tests/test_pdf_baseline.py` |
| 文書 | `README.md`, `docs/architecture/data_model.md`, `docs/architecture/system_architecture.md`, `docs/handoff/current_status.md`, `docs/development/db_migrations.md`, `docs/redesign/v0.4/README.md`, `docs/redesign/v0.4/knowledge_growth_implementation_tasks_v0_4.md`, `docs/redesign/v0.4/phase0_baseline.md` |

## 最終テスト結果

```bash
.venv/bin/python -m pytest tests/ -q
# 21 passed, 1 warning
```

`bash scripts/setup_codex_cloud.sh` も修正後に正常終了。v1 SQLと変更前のSCHEMAの
完全一致、文書リンクの解決、`git diff --check` も確認した。

| 対象 | 件数 | 結果 |
|---|---:|---|
| 既存smoke / 本文抽出 | 4 | 全件成功 |
| DB migration・保全・backup・rollback・CLI | 13 | 全件成功 |
| 生成PDF・PDF API workflow / 再起動 | 4 | 全件成功 |

Python 3.12.14 / FastAPI 0.142.2 / Starlette 1.7.0 / httpx 0.28.1 /
PyMuPDF 1.28.2 / pytest 9.1.1。APIキー不要のmockで検証。
1 warningはStarlette TestClientのhttpx利用に関する非推奨通知。
依存関係の移行はこのPhaseで行っていない。

## ブラウザ確認

Chromium 151.0.7922.173 + Playwright、1440×1000、ローカルuvicorn + mockで確認。
`KG_DATA_DIR` は専用の `/tmp/kg-phase0-browser-data` を使用し、私有PDFは使用していない。
生成した `visual_evidence.pdf` をアップロードして以下が成功した。

| 操作 | 確認した結果 |
|---|---|
| PDF登録・表示 | 2ページのcanvasと選択可能なtext layerを表示 |
| テキスト表示で選択→説明 | SourceAnchor付きmock回答を表示 |
| 回答の原文リンク | 対応ブロックへ戻れる |
| 回答全体を保存 | ノートで保存したmock回答を表示 |
| 段落の訳ボタン | 原文直下にmock翻訳を表示 |
| MD出力・JSON export | MDダウンロードと保存項目の出所を確認 |
| リロード | PDF表示モード、scrollTop=350、ノートタブを復元 |
| 再訪後の本文・対話 | 翻訳とQ&Aが残ることを確認 |
| JavaScript例外 | pageerrorなし |

ブラウザ確認は今回の手動E2E記録。常設のブラウザテスト基盤は追加していない。
APIでの再起動テストだけではcanvasやlocalStorageを検証できないため、分けて記録する。

## 残課題と次のPhase

- 次のReader開発は **Phase 1: T1-01〜T1-03（PDF構造・bbox・Anchor強化）**。
  2段組fixtureは準備済みだが、読み順の改善・bbox保存は未実装。
- 図表・数式は原PDFで閲覧できるが、テキスト抽出では構造化されない。
  Figureのラベルや数式を見出し扱いする現行ヒューリスティックもそのまま。
- **Phase 0A: T0A-01〜T0A-05** は別の基盤作業として未着手。
  現行はmock既定で起動・テストにAPIキー不要。Codex app-server接続、subscription認証、
  既存mock案内等のsubscription-firstへの更新は未実装。
- AI能力はmockでの配線・永続化だけを検証した。実際の翻訳品質やAI接続は未検証。
- Windowsでの移行・復旧と、実際の利用者DBでは未検証。生成した旧schema DBで保全を確認した。
- backupはDBのみ。PDFファイルとlocalStorageの保全・downgrade時の停止手順が必要。
