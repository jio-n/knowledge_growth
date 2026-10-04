# テスト手順

## 自動テスト

```bash
.venv/Scripts/python -m pytest tests/ -q
```

- ネットワーク・APIキー不要(`KG_LLM_PROVIDER=mock`、一時ディレクトリDBを使用 — 本番 `data/` に触れない)
- `tests/test_smoke.py` がE2Eフローを検証: テキスト資料登録 → 初期抽出完了 → ブロック取得 → アンカー付き質問 → モック回答 → 知識保存 → ノート反映 → 編集でorigin遷移(llm→llm_edited) → Markdownエクスポート内容 → 横断検索 → 重複検出 → 全体JSONエクスポート

## 手動確認(リリース前チェックリスト)

1. `python run.py` → http://localhost:8300 がライブラリを表示
2. [+ テキスト] でMarkdown貼付 → 読解画面へ遷移、概要タブに抽出結果
3. [+ URL] で実在ページ(例: arXiv abs ページ)登録 → 本文ブロック表示
4. [+ PDF] でPDFアップロード → PDF描画、テキスト選択可能
5. 原文選択 → ポップオーバー → [説明] → 対話タブに回答
6. 回答の一部を選択 → [選択部分を保存] → ダイアログ(保存先プリセレクト) → 保存 → ノートタブに出現
7. ノート項目の 📍 → 原文該当箇所へスクロール+フラッシュ
8. 段落hover [訳] → 原文直下に訳文
9. ブラウザ再読込 → 状態が残っている(F-14)
10. [Markdownで見る] → プレビュー・ダウンロード
11. 同じ内容を再登録 → 重複ダイアログ
12. ヘッダー検索で保存した知識が見つかる

## 実LLMでの確認

`KG_LLM_PROVIDER=anthropic`(+キー)で 5・6 を再実行し、回答に原文引用と「資料によると/解釈:」の区別が含まれることを確認。

## Paper Brief / Import foundation

`tests/test_import_bridge.py`はAPIキー・PDF原本なしのvalidator、生成PDFとの照合、
Python/ブラウザAnchorの一致、preview/commit、競合、stale、二重import、rollback、危険ZIPを検証する。
`tests/test_migrations.py`はv2→v3のbackup/rollbackと既存研究データ保全も検証する。
fixture生成: `.venv/bin/python scripts/generate_kgpack_fixtures.py --output /tmp/kgpack-fixtures`。
CLI確認: `.venv/bin/python -m app.import_bridge validate /tmp/kgpack-fixtures/valid.kgpack`。

## Import確認UI / Paper Brief browser smoke

```bash
.venv/bin/pip install playwright  # optional development dependency
.venv/bin/python scripts/smoke_import_brief_browser.py --chromium /usr/bin/chromium
```

一時DB・APIキーなしmockの実サーバーをscriptが起動し、generated PDFとkgpackのみで検証する。
validate、strong/weak/ambiguous/unmatched、手動選択、field除外、user編集保持、Evidence候補の閲覧、
未解決/ページのみ、duplicate、stale、commit failure/retry、再確認、Brief/status/Key Result、PDF bbox jump、
再読込、狭幅dialog、入力HTMLを実行しないことを確認する。
スクリーンショットは`/tmp/kg-import-brief-browser/`。終了時に一時DBを削除し、通常のdata/を使わない。
[UI契約・既知の制約](../redesign/v0.4/import_brief_ui.md)。
