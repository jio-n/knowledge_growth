# Research Reading Workspace(knowledge_growth)

論文・研究資料(PDF / Webページ / テキスト / Markdown)を読み、原文を確認しながらLLMに質問し、**残したい知識だけ**を資料ごとの理解ノートへ保存・蓄積する、ローカルファーストの研究読解ワークスペース。

- チャットではなく**資料が中心単位**。再訪すると前回の理解ページがそのまま開く
- すべての知識・回答は**原文アンカー**で根拠箇所へ戻れる
- 出所(原文 / AI回答 / AI編集済 / 自分)と情報種別を常に区別
- 理解ノートはMarkdown / JSONでエクスポート可能(ロックインなし)
- LLMプロバイダー交換可能(Anthropic / OpenAI互換 / オフラインmock)

## クイックスタート(Python 3.11+ のみ必要)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows(macOS/Linux: .venv/bin/pip)
python scripts/fetch_vendor.py                  # pdf.js / marked.js を取得(初回のみ)
python run.py                                   # → http://localhost:8300
```

APIキーなしで全機能が動作します(mockプロバイダー)。実LLMを使う場合は [docs/development/setup.md](docs/development/setup.md) を参照。

## ドキュメント

| 目的 | 場所 |
|------|------|
| 何を作っているか | [docs/product/product_vision.md](docs/product/product_vision.md) |
| 要件 | [docs/product/requirements.md](docs/product/requirements.md) |
| アーキテクチャ | [docs/architecture/system_architecture.md](docs/architecture/system_architecture.md) |
| 開発の現在地・次の作業 | [docs/handoff/current_status.md](docs/handoff/current_status.md) |
| 開発ガイド(AI/人間共通) | [CLAUDE.md](CLAUDE.md) |
