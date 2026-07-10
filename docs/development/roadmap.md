# ロードマップ

優先度・根拠は docs/product/improvement_proposals.md 参照。MVP完了後の推奨順:

## フェーズ2(MVP直後)
1. ストリーミング回答(B-2) — 体感品質
2. DOI/arXiv APIメタデータ補完(B-1)
3. Obsidian/ローカルVault自動エクスポート(B-3)
4. タスク別モデル設定(B-4)

## フェーズ3
5. Web再取得+差分検出(C-3 — source_versionsが受け皿として設計済み)
6. 意味検索(C-1 — 検索APIの置換で済むようGET /api/searchの契約は維持)
7. 複数資料比較ビュー(C-2)
8. PDF bounding boxアンカー・図表画像抽出(C-4, C-6)

## フェーズ4(調査後)
9. 知識グラフ/引用関係(D-1)、Zotero連携(D-2)
10. 再読支援・理解度トラッキング(C-5, C-7)

## 技術的負債・化粧直し候補
- マイグレーション機構(現状 CREATE IF NOT EXISTS のみ)
- FTS5全文検索(LIKEの置換)
- 認証層(localhost外へ公開する場合の前提条件)
- PDF見出し検出の精度(2段組対応)
