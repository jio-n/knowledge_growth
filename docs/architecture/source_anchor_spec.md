# 原文アンカー仕様(SourceAnchor)

最終更新: 2026-07-11 / 状態: 確定(v1)。W3C Web Annotation の TextQuoteSelector を参考に、複数の手がかりを冗長に持つことで再解析・ページ更新後もリンク復元できるようにする(§14)。

## JSON スキーマ

```json
{
  "type": "text-quote",
  "quote":  "選択された原文そのもの(最大500字)",
  "prefix": "直前の文脈(最大60字)",
  "suffix": "直後の文脈(最大60字)",
  "blockId":  "document_blocks.id(最優先の解決キー)",
  "blockIdx": 12,
  "page": 5,
  "headingPath": "3 Method > 3.2 Loss"
}
```

すべてのフィールドは任意(ベストエフォート)。ただし作成時は取得可能なものを全部埋めること。

## 作成(クライアント側)

- ブロック表示(web/text/markdown/PDF代替テキスト): 選択範囲を含むブロック要素から blockId/blockIdx/headingPath/page を取り、quote=選択文字列、prefix/suffixはブロックテキスト内の前後から切り出す。
- PDF(pdf.jsテキストレイヤー): 選択スパンの属するページ番号 + quote + 対応する抽出ブロック(後述の対応付けで最も近いもの)の blockIdx。

## 解決(復元)アルゴリズム — 上から順に試行、成功した時点で終了

1. **blockId** が現行版に存在 → そのブロックへスクロールし、ブロック内で quote を部分一致ハイライト。
2. **blockIdx** が現行版の範囲内で、そのブロックテキストに quote(先頭80字)が含まれる → 同上。
3. **quote 全文検索**: 全ブロックから quote(先頭80字、空白正規化)を検索。複数一致時は prefix/suffix の一致度で選ぶ。
4. **page**(PDF)→ 当該ページ先頭へスクロール(引用ハイライトなし)。
5. すべて失敗 → 資料先頭 + 「原文位置を特定できませんでした(資料が更新された可能性)」のトースト表示。**リンク切れでも知識項目自体は失われない。**

空白正規化: 連続空白を1つに、前後trim してから比較する。

## 設計理由

- blockId は高速だが版に依存する。quote+prefix/suffix は版をまたいで生き残る。両方持つことで「速い通常時 + 頑健な劣化時」を実現。
- Webページ更新で再取得(将来機能)しても、quote ベースの解決が新版ブロックに対して動く。
- bounding box(PDF座標)は将来拡張(roadmap)。現行はページ+テキスト検索で十分な精度。

## 実装箇所

- 作成: `client/js/components/selection.js`(makeAnchor)
- 解決: `client/js/views/reader.js`(resolveAnchor)— ブロック表示とPDF表示の両方を扱う
- 保存形式: questions.anchor / knowledge_items.anchor / highlights.anchor(いずれもJSON文字列)
