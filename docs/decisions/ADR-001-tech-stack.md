# ADR-001: 技術スタック — Python/FastAPI + ビルド不要Vanilla JS + SQLite

日付: 2026-07-11 / 状態: 採用

## 文脈
開発環境(Windows 11)には Python 3.13 と git のみ存在し、Node.js が無い。要件は「別の開発者・別のAIがローカルで起動できる」「ベンダーロックインなし」「ローカルファースト」。

## 決定
- バックエンド: Python 3.11+ / FastAPI / uvicorn。依存はすべて pip wheel で入るもののみ。
- DB: stdlib sqlite3(ORMなし)。単一ファイル `data/knowledge.db`。
- フロントエンド: ビルドステップなしの Vanilla JS (ES Modules)。pdf.js と marked.js は `client/vendor/` にベンダリング(取得スクリプト `scripts/fetch_vendor.py` で再現可能)。
- LLM: SDKを使わず httpx で直接API呼び出し。

## 理由
1. **セットアップ最小**: `python -m venv` + `pip install` + `python run.py` だけ。Nodeツールチェーン(インストール・ビルド・依存更新)を排除。
2. **継続開発性**: フレームワークのバージョン腐敗リスクが低い。Vanilla JSはどのAIエージェント/開発者でも読める。
3. **ローカルファースト/可搬性**: SQLite単一ファイル+ファイルディレクトリ=バックアップはコピーで完結。

## 代替案と却下理由
- React+Vite: UI開発効率は高いが Node 必須+ビルド成果物管理が増える。環境に Node が無い事実が決定打。
- Electron/Tauri: 配布は良いが開発・ビルドが重い。ブラウザで十分。
- PostgreSQL: 単一ユーザーローカルにはオーバーキル。
- LLM公式SDK: ロックイン+依存増。HTTP APIは安定しており素のhttpxで十分。

## 帰結
- リッチなUIコンポーネントは自前実装(コスト増を ui_spec.md の精密化で抑制)。
- 将来 React 等へ移行する場合も API 契約(api_spec.md)がそのまま使える。
