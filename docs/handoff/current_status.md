# 現在地(handoff)

最終更新: 2026-07-11(設計正本コミット時点)

## 状態サマリ

- 設計文書: **完了**(docs/product, docs/architecture, docs/decisions)
- バックエンド: 約70%(config/db/ingest/llm/prompts/context/analysis/sources/qa 実装済)
- 未実装(バックエンド): `app/routes_knowledge.py`(知識CRUD・suggest・note組立・検索)、`app/routes_export.py`、`app/main.py`、`run.py`、`scripts/fetch_vendor.py`、`tests/`
- フロントエンド: 未着手(仕様は docs/product/ui_spec.md に完備)
- 検証: 未実施

## 進行中の開発方式

ADR-005 参照。実装タスクはコミット済み設計文書を仕様としてサブエージェント(または任意の開発者)へ委譲し、オーケストレーターがレビュー・統合する。

## 次の作業

1. バックエンド残り(api_spec.md 準拠)
2. フロントエンド一式(ui_spec.md + api_spec.md + source_anchor_spec.md 準拠)
3. vendor取得スクリプト + 実行
4. pytest スモークテスト(mockプロバイダー、E2E: 登録→質問→保存→ノート→エクスポート)
5. ブラウザでの動作確認
6. docs/development/*(setup/testing/implementation_status/known_issues/roadmap)と handoff 最終化
