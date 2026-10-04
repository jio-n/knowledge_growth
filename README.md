# Research Reading Workspace(knowledge_growth)

論文・研究資料(PDF / Webページ / テキスト / Markdown)を読み、原文を確認しながらLLMに質問し、**残したい知識だけ**を資料ごとの理解ノートへ保存・蓄積する、ローカルファーストの研究読解ワークスペース。

- チャットではなく**資料が中心単位**。再訪すると前回の理解ページがそのまま開く
- すべての知識・回答は**原文アンカー**で根拠箇所へ戻れる
- 出所(原文 / AI回答 / AI編集済 / 自分)と情報種別を常に区別
- 理解ノートはMarkdown / JSONでエクスポート可能(ロックインなし)
- AI runtime交換可能(Codex app-server + ChatGPT plan / no-AI / オフラインmock、API key不要)

## Windows: ワンクリック起動

アプリ起動の前提は **Python 3.11+** のみです。開発用のPhase 1 resolverテストにはNode.js 18+を使います。

`start_knowledge_growth.bat` をダブルクリックしてください。

初回起動時に自動で:

1. `.venv` を作成
2. `requirements.txt` をインストール
3. pdf.js / marked.js を取得
4. アプリを起動
5. `http://127.0.0.1:8300` をブラウザで開く

2回目以降は準備済み環境を再利用します。依存定義が変わった場合は自動で再セットアップします。

## 手動セットアップ

```bash
python scripts/bootstrap_dev.py --setup --test
python run.py
```

従来手順でも起動できます:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows(macOS/Linux: .venv/bin/pip)
python scripts/fetch_vendor.py                  # pdf.js / marked.js を取得(初回のみ)
python run.py                                   # → http://localhost:8300
```

## Codex Cloud

Cloud環境の Setup command には次を指定します。

```bash
bash scripts/setup_codex_cloud.sh
```

このスクリプトは仮想環境、依存関係、ブラウザ用vendorファイルを準備し、既存pytestを実行します。

Cloud開発では実際に読む論文や私有PDFをfixtureとしてGitへ追加せず、**生成PDFまたは明示的に再利用可能なテスト資料**を使います。

## Redesign v0.4

現在実装済みのJuly 2026 MVPを土台に、論文Readerの再設計を進めます。

設計の入口:

- [docs/redesign/v0.4/README.md](docs/redesign/v0.4/README.md)
- [MVP仕様 v0.4](docs/redesign/v0.4/knowledge_growth_mvp_spec_v0_4.md)
- [実装タスク一覧 v0.4](docs/redesign/v0.4/knowledge_growth_implementation_tasks_v0_4.md)
- [Paper Brief Schema](docs/redesign/v0.4/paper_brief_schema_v0_1.md)
- [ChatGPT Import Bridge](docs/redesign/v0.4/import_bridge_spec_v0_1.md)
- [ADR-006: Subscription-first AI Runtime](docs/decisions/ADR-006_subscription_first_ai_runtime_and_import_bridge.md)

**v0.4の方針:** 通常利用でAPI従量課金を前提にしません。第一候補は Codex app-server + ChatGPT plan、ChatGPT Import BridgeもMVP対象です。これは再設計の目標仕様であり、現行baselineにすべて実装済みという意味ではありません。

Phase 0（baseline確認・DB migration基盤・生成PDF fixtures）は実装済みです。
[検証結果と残課題](docs/redesign/v0.4/phase0_baseline.md)、
[移行・backup・復旧手順](docs/development/db_migrations.md)、
[生成PDFの使い方](tests/fixtures/README.md) を参照してください。

## AI接続（Phase 0A）

既定はCodex app-server + ChatGPT planです。Codex未インストール・未認証でもアプリは起動し、
原PDF、kgpack Import、Import済みPaper Brief、Note、Highlight、exportを使えます。
AI操作にはheaderの「AI」→「ChatGPTで接続」からbrowser認証してください。API keyは不要です。
認証はCodexのOS keyringを使用し、アプリのSQLite/configへtokenを保存しません。
keyringが使えない環境ではno-AI/Importを使用できます。

`KG_AI_RUNTIME=no_ai`で明示的なoffline利用、`KG_AI_RUNTIME=mock`で開発用mockに切り替えられます。
[実装・protocol・tests・manual smoke・制約](docs/redesign/v0.4/subscription_first_runtime_foundation.md)を参照。

## Paper Brief生成

登録済みPDFを開き、Paper Briefタブの「Paper Briefを生成」から抽出できます。
ChatGPT接続後に生成結果とEvidenceを確認し、「確認したBriefを保存」で反映します。
再生成ではユーザー編集を保持します。AI未接続でも既存Brief・PDF・kgpackを利用できます。
[生成pipeline・tests・manual smoke・制約](docs/redesign/v0.4/structured_brief_generation.md)を参照してください。

## ドキュメント

| 目的 | 場所 |
|------|------|
| 再設計v0.4の入口 | [docs/redesign/v0.4/README.md](docs/redesign/v0.4/README.md) |
| 現行MVPで何を作っているか | [docs/product/product_vision.md](docs/product/product_vision.md) |
| 現行要件 | [docs/product/requirements.md](docs/product/requirements.md) |
| 現行アーキテクチャ | [docs/architecture/system_architecture.md](docs/architecture/system_architecture.md) |
| 開発の現在地・次の作業 | [docs/handoff/current_status.md](docs/handoff/current_status.md) |
| Codex向け開発ガイド | [AGENTS.md](AGENTS.md) |
| Claude/人間共通の既存開発ガイド | [CLAUDE.md](CLAUDE.md) |
