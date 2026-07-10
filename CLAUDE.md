# Research Reading Workspace — 開発ガイド(AIエージェント・人間共通)

論文・研究資料を読み、質問し、選んだ知識を理解ノートへ育てる、資料中心のローカルファーストワークスペース。

## 最初に読むもの(順番)

1. `docs/handoff/current_status.md` — 今どこまでできているか・次にやること
2. `docs/product/product_vision.md` — 何を作っているか(non-goals含む)
3. `docs/architecture/system_architecture.md` — モジュールマップ
4. 作業対象に応じて: `docs/architecture/api_spec.md` / `docs/product/ui_spec.md` / `docs/architecture/data_model.md` / `docs/architecture/source_anchor_spec.md` / `docs/architecture/ai_pipeline.md`

## 起動

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python scripts/fetch_vendor.py     # 初回のみ: pdf.js / marked.js を client/vendor/ へ取得
python run.py                      # → http://localhost:8300
```

LLMは既定で `mock`(キー不要で全機能動作)。実LLMは `config/app.config.json` の `llm.provider` を変更し対応する環境変数(ANTHROPIC_API_KEY 等)を設定。

## テスト

```
.venv\Scripts\python -m pytest tests/ -q
```

テストは常に mock プロバイダーで動く(ネットワーク・キー不要)。

## 開発規約

- **docs/ が正本**(ADR-005)。実装と文書がズレたら必ず同期させる。スキーマ変更は `app/db.py` と `docs/architecture/data_model.md` を同時に。API変更は `docs/architecture/api_spec.md` を同時に。
- 設計判断は `docs/decisions/ADR-*.md` に追記。未解決事項は `docs/handoff/open_questions.md`。
- 作業終了時は `docs/handoff/current_status.md` と `docs/development/implementation_status.md` を更新してからコミット。
- プロンプトは `prompts/*.md` のみ(コードへの埋め込み禁止)。変更時は frontmatter の version を上げる。
- LLM呼び出しは必ず `app/llm/` 経由(プロバイダー直呼び禁止)。
- フロントエンドはビルド不要 Vanilla JS を維持(Node依存を持ち込まない — ADR-001)。
- 資料本文のDOM挿入は textContent のみ(XSS)。AI回答の表示のみ marked を使用。
- `knowledge_items` の origin='auto_extract' 以外を自動処理で書き換えない(§21、data_model.md 不変条件)。

## ディレクトリ

```
app/        FastAPIバックエンド    client/   フロントエンド(vanilla JS)
prompts/    プロンプト正本         config/   設定(LLM・ノートテンプレート)
docs/       設計・引き継ぎ正本      tests/    pytest(mockで動作)
data/       実行時データ(git外)    scripts/  補助スクリプト
```
