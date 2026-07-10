# 次の作業候補(優先順)

最終更新: 2026-07-11。背景・根拠は improvement_proposals.md / roadmap.md。

1. **実LLMでの品質確認**(ユーザー作業): config で anthropic/openai_compat に切替え、実論文で質問・抽出・翻訳の品質を確認。プロンプト(prompts/*.md)の改善はここから始まる
2. **ストリーミング回答**(B-2): SSEエンドポイント追加 + qa.js の逐次描画。体感品質の最大改善
3. **ingest自動テスト追加**(known_issues #12): fixture PDF/HTMLで pdf.py / web.py の単体テスト
4. **DOI/arXiv メタデータ補完**(B-1): 登録後に Crossref / arXiv API で書誌を補完(失敗しても登録成立)
5. **Obsidian/Vault自動エクスポート**(B-3): 設定に出力先ディレクトリ、ノート更新時に export.md を書き出し
6. **タスク別モデル設定**(B-4): llm設定を task→provider/model のマップに拡張
7. **PDF表示モードのハイライト描画**(known_issues #4)

変更時の必須事項: 対応する docs を同時更新(CLAUDE.md の規約参照)。
