# 情報アーキテクチャ

最終更新: 2026-07-11

## 情報管理の中心単位: 資料(ResearchSource)

```
資料 (sources)
 ├─ 原文        … 取得版(source_versions) → ブロック列(document_blocks) + 原本ファイル(data/files/)
 ├─ 翻訳        … translations(ブロック対応、AI訳/ユーザー修正を区別)
 ├─ 対話        … questions → answers(モデル・プロンプト・使用コンテキスト記録)
 ├─ ハイライト   … highlights(アンカー付き)
 ├─ 理解ノート   … knowledge_items の section_key 別射影(ADR-004)
 │                各項目: 出所 origin / 種別 info_type / 検証状態 / 📍アンカー / 由来Q&A
 └─ 分類        … tags、reading_status、importance
```

チャットスレッドという単位は存在しない。対話はすべて資料に従属する(§22.1)。

## 双方向リンク(§14)

- ノート項目 → 📍アンカー → 原文ブロック/PDFページ
- ノート項目 → 由来Q&A(対話タブの該当カード)
- 対話カード → 📍選択箇所 → 原文
- 原文ハイライト → (クリック) → コメント/削除
- 逆方向(原文→関連ノート項目)は将来拡張(roadmap: ブロックサイドマーカー)

## 横断軸(資料をまたぐ)

- タグ / 読書状態 / 横断検索(タイトル・知識・質問・回答)
- 将来: 意味検索、比較ビュー、知識グラフ(roadmap.md)

## 出所の区別(§13) — UI・エクスポートの両方で保持

origin(5値)×info_type(11値)を knowledge_items に必須付与。バッジ色は ui_spec.md、エクスポート時ラベルは export_spec.md 参照。
