# 現在地(handoff)

最終更新: 2026-07-11 / **状態: MVP完成・E2E検証済み**

## 何ができているか

§30の完了条件を満たすMVP。`python run.py` → http://localhost:8300 で以下が動く:

登録(PDF/URL/テキスト・重複検出) → ライブラリ → 読解画面(PDF描画 or ブロック表示) → 構造化初期抽出 → 選択→質問(9種操作) → AI回答(モデル・根拠記録) → 全体/部分保存(AI保存先提案) → 理解ノート(出所バッジ・📍原文ジャンプ) → 翻訳(原文直下) → Markdown/JSONエクスポート → 再訪時の完全復元 → 横断検索。

LLMは既定mock(キー不要)。実LLM切替は docs/development/setup.md。

## どう検証したか

docs/development/implementation_status.md の検証ログ参照。pytest 4件 + ブラウザE2E + 実arXivページ取り込み。

## 開発の再開方法(誰でも/どのAIでも)

1. CLAUDE.md → このファイル → docs/development/implementation_status.md を読む
2. 環境: docs/development/setup.md(Python 3.11+のみ)
3. 次にやること: docs/handoff/next_actions.md
4. 要確認事項: docs/handoff/open_questions.md
5. 開発方式: ADR-005(docs/が正本。実装と文書を常に同期)

## 開発履歴の要点

- 2026-07-11: 設計正本(docs/)作成 → バックエンド → フロントエンド(Sonnetサブエージェント2体が仕様書ベースで実装、オーケストレーターがレビュー・統合・E2E検証)。フロントエンド実装時にライブ検証で5件のバグを検出・修正済(highlight markの属性欠落、anchor JSON文字列/オブジェクト不一致、authors JSON文字列での.join例外、suggest応答による明示的セクション選択の上書き、ルーターの競合レンダリング)。
