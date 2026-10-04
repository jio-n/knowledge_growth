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

## AI Runtime設定

既定は `runtime.type=codex_chatgpt_plan`。OpenAI/Anthropic等のAPI keyは要求しません。
Codex CLIをインストールすると、アプリがapp-serverをchild processとして起動します。
未導入でもPDF・Note・Highlight・Import・Brief・exportを利用できます。

headerのAI状態メニュー → 「ChatGPTで接続」 → browser認証リンクから接続します。
CodexのOS keyringへ認証を保存します。keyringが使えない場合、平文保存へfallbackせず
no-AIまたはChatGPT Import Bridgeで利用してください。既存Codexのfile保存認証は自動移行しません。

| 設定 | 用途 |
|---|---|
| `KG_AI_RUNTIME=codex_chatgpt_plan` | ChatGPT plan runtime（既定） |
| `KG_AI_RUNTIME=no_ai` | 明示的なoffline利用 |
| `KG_AI_RUNTIME=mock` | 開発・テスト用、API key/account不要 |
| `KG_LLM_PROVIDER=mock` | 既存テスト用の互換設定 |
| `KG_CODEX_EXECUTABLE` | PATHで見つからないnative binaryのパス |

Windowsではnpmのcodex.cmdをshell経由で起動しません。package内のcodex.exeを探索し、
見つからなければnative binaryのパスを指定してください。
詳細とoptional実接続確認は[Phase 0A記録](../redesign/v0.4/subscription_first_runtime_foundation.md)を参照。

## データの場所

- `data/knowledge.db` — SQLite(全構造化データ)
- `data/files/` — PDF原本・HTMLスナップショット
- バックアップ = `data/` フォルダをコピーするだけ。完全退避は `GET /api/export/all.json` も参照(docs/architecture/export_spec.md)

## トラブルシューティング

- `pip install` で lxml/pymupdf が失敗 → Pythonバージョンが新しすぎる/古すぎる可能性。3.11–3.13で確認済み
- ポート8300が使用中 → `run.py` の port を変更
- PDF表示が真っ白 → `client/vendor/` が空。`python scripts/fetch_vendor.py` を実行
