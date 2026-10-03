# 原文アンカー仕様（SourceAnchor）

更新: 2026-10-04 / redesign v0.4 Phase 1（T1-03）。保存先は従来どおり
questions.anchor / knowledge_items.anchor / highlights.anchor のJSON文字列。
既存JSONをmigrationで書き換えない。

## JSON形式

```json
{
  "type": "text-quote",
  "sourceVersion": "source_versions.id",
  "sourceHash": "source_versions.content_hash",
  "blockId": "document_blocks.id",
  "blockIdx": 12,
  "page": 5,
  "bbox": {
    "coordinate_system": "pymupdf_unrotated",
    "units": "pt",
    "rect": [40, 145, 555, 190],
    "page_rect": [0, 0, 595, 842],
    "rotation": 0
  },
  "quote": "選択された原文（最大500字）",
  "prefix": "引用直前（最大60字）",
  "suffix": "引用直後（最大60字）",
  "headingPath": "3 Method > 3.2 Loss"
}
```

全フィールドは任意。quote/prefix/suffixは空白を正規化し、quoteは保存された全文で比較する。
先頭80字への切り詰めや、同点候補から先頭を選ぶ処理は行わない。
headingPathは表示の手がかりであり、それだけで根拠の一致を確定しない。

## 作成

- 原文ブロック: version、source hash、ID/index、page、ブロックbbox、quoteと前後文脈。
- PDF text layer: 選択矩形をPDF.js viewportで逆変換し、ページ左上原点の未回転座標へ戻す。
  page + bbox + quoteが一意に解決できる場合だけID/index/headingPathを補う。
  ページ先頭ブロックへの近似割当は行わない。描画順のprefix/suffixは本文順と違うため保存しない。
- 複数ページ・複数ブロックをまたぐ選択に単一ブロックを割り当てない。
- ブロックbboxとPDF選択bboxでは粒度が異なる。どちらも同じ座標契約を使う。

## 解決優先度と安全条件

`client/js/anchor.js` の純粋関数 `resolveSourceAnchor(anchor, blocks, version)` を、
Readerの根拠ジャンプと保存済みhighlightで共用する。

1. **sourceVersion + blockId**: 現行versionとblock.version_idが一致すること。
   quoteがあれば全文がブロックに含まれることも確認する。
2. **page + bbox**: 座標形式・ページ寸法が一致し、交差面積/小さい方の矩形面積が0.8以上の
   ブロックを探す。quoteがあれば前後文脈を含めて一致するものだけを対象にする。
   一意な候補に限って確定し、versionまたはsource hashが一致しない場合は、文書全体でも
   quoteと文脈が一意に一致することを要求する。異なる原本に座標だけで確定しない。
3. **quote + prefix + suffix**: 全ブロックの全出現位置を比較。前後文脈がある場合は
   それぞれの全文一致を要求する。重複が残ったら候補表示。同一ブロック内の重複も数える。
4. **blockIdx**: 現行versionが一致する場合のみ。quoteがあれば一致が必要。
   曖昧なbbox/quoteをindexで上書きしない。
5. **page**: 存在するページへの粗い移動。根拠は未解決と明示し、引用をhighlightしない。
6. **候補 / unresolved**: 候補は確認ボタンとして表示する。選択はその場の閲覧だけで、
   保存済みAnchorの自動書換えやverification変更は行わない。

返値は `status: resolved | candidates | page_only | unresolved`、`method`、
`block`、`page`、`candidates`、`reason`。resolved以外は確定根拠扱いしない。
旧Anchorはquote等の可搬selectorで解決する。versionのない裸のID/indexだけでは自動確定せず、
pageまたはunresolvedへ劣化する。情報不足でもQ&A・ノート自体は削除しない。

## 表示と永続化

- PDFの確定根拠は対象ブロックのbboxへ移動し、一時的な領域枠を表示する。
  文字列の最初の出現位置へのジャンプは根拠解決に使わない。
- bboxがない旧ブロックはページまで移動できる。正確なPDF領域の表示は保証しない。
- highlightはresolvedブロックにのみ適用。ブロック内の生文字列quoteが複数ある場合は
  markを適用せず、別の出現位置を誤って塗らない。
- Anchor解決結果は閲覧時に計算し、既存のユーザーJSONやノートを自動変更しない。

## 実装範囲

PDF再取得・差替え・再抽出のAPI/UIは今回追加しない。既存 `/reanalyze` はAI解析であり、
document_blocksを再生成しない。新versionでの再抽出はgenerated fixturesとDBテストで模擬する。
本resolverはクライアントのEvidence navigationとhighlightに適用する。
Q&Aの既存context builderにも安全条件を追加し、現行versionのID/indexか、全文quoteの
一意一致を確認できた場合のみ周辺本文を渡す。曖昧な引用や裸の旧indexからは周辺本文を
推測しない。検索対象も最新versionに限定する。Context Builder v2やAI runtimeは未実装。

## Import Bridgeのbackend resolver

`app/source_anchor.py`は上記Phase 1契約をPythonで適用する。
外部packageのID/indexは使用せず、可搬selectorから現行versionのAnchorを生成する。
field/Key Resultのconfirmedはresolvedのみで保持し、verificationは昇格しない。
[可搬Evidence契約](../redesign/v0.4/paper_brief_import_foundation.md#evidence-algorithm)を参照。
