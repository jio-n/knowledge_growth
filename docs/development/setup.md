# セットアップ手順

前提: Python 3.11+(開発は3.13)、git。アプリ起動・buildにNode.jsは不要。Phase 1のpytestには、実際のbrowser resolverを実行するためNode.js 18+が必要。

```bash
git clone <repo> && cd knowledge_growth
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Windows。macOS/Linux: .venv/bin/pip
python scripts/fetch_vendor.py                    # pdf.js / marked.js を client/vendor/ へ(初回のみ、要ネットワーク)
python run.py                                     # → http://localhost:8300
```

`.venv` を使わず既存環境に入れても動くが、venv推奨。

## LLMプロバイダー設定

既定は `mock`(キー不要、全フロー動作、回答はモックラベル付き)。実LLMを使う場合、`config/app.config.json` の `llm.provider` を変更:

| provider | 必要な環境変数 | 備考 |
|----------|----------------|------|
| `anthropic` | `ANTHROPIC_API_KEY` | モデルは `llm.anthropic.model`(既定 claude-sonnet-5) |
| `openai_compat` | `OPENAI_API_KEY`(ローカルLLMなら不要) | `base_url` 変更で Ollama(`http://localhost:11434/v1`)/LM Studio/vLLM に接続可 |
| `mock` | なし | 開発・テスト・デモ用 |

環境変数 `KG_LLM_PROVIDER` で一時的に上書き可能(設定ファイルより優先)。APIキーを設定ファイルに書かないこと。

## データの場所

- `data/knowledge.db` — SQLite(全構造化データ)
- `data/files/` — PDF原本・HTMLスナップショット
- バックアップ = `data/` フォルダをコピーするだけ。完全退避は `GET /api/export/all.json` も参照(docs/architecture/export_spec.md)

## トラブルシューティング

- `pip install` で lxml/pymupdf が失敗 → Pythonバージョンが新しすぎる/古すぎる可能性。3.11–3.13で確認済み
- ポート8300が使用中 → `run.py` の port を変更
- PDF表示が真っ白 → `client/vendor/` が空。`python scripts/fetch_vendor.py` を実行
