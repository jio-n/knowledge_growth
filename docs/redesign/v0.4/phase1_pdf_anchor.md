# Phase 1 PDF / Anchor 実装・検証記録

更新: 2026-10-04 / 対象: T1-01〜T1-03 / baseline: `8f93465`（Phase 0 merge済みmain）

## 1. 変更ファイル

| 分類 | ファイル |
|---|---|
| PDF / DB / API | `app/ingest/pdf.py`, `app/ingest/common.py`, `app/migrations/002_pdf_evidence_geometry.sql`, `app/migrations/__init__.py`, `app/routes_sources.py` |
| Anchor / Q&A安全確認 | `client/js/anchor.js`, `client/js/views/reader.js`, `client/js/components/selection.js`, `client/js/components/docviewer.js`, `client/js/components/pdfviewer.js`, `client/css/app.css`, `app/context.py`, `app/routes_qa.py` |
| 検証 | `tests/test_pdf_geometry.py`, `tests/test_migrations.py`, `tests/fixtures/pdf_factory.py`, `tests/fixtures/README.md`, `scripts/smoke_phase1_browser.py` |
| 文書 | `README.md`, `docs/architecture/data_model.md`, `docs/architecture/source_anchor_spec.md`, `docs/development/db_migrations.md`, `docs/development/setup.md`, `docs/handoff/current_status.md`, `docs/redesign/v0.4/README.md`, `docs/redesign/v0.4/knowledge_growth_implementation_tasks_v0_4.md`, 本書 |

`001_baseline.sql` は変更していない。実論文・購入PDF・私有PDF・runtime dataは追加していない。

## 2. タスクごとの実装

- **T1-01**: PyMuPDFのblock/line/spanから本文と座標を取得。元の抽出ブロック境界を維持し、
  同じ左端で近接する同種の行のみ結合。列・asset・captionをまたぐ小段落の無条件結合を廃止。
  caption、数式、埋め込み画像、接続するvector描画の領域候補を保持する。
- **T1-02**: bbox_json / roleをv2 migrationで追加。既存versionのcontent_hashを再利用し、
  source_hash列の重複を避けた。parent_block_id / asset_refは未実装の実体に依存するため保留。
  APIはbbox_jsonとパース済みbboxを返す。新規block IDをversionと内容・座標から安定生成する。
- **T1-03**: 根拠ジャンプと保存済みhighlightが共通resolverを使用する。version / source hash /
  bboxを新規Anchorに保存し、PDF選択をページ先頭blockへ近似割当しない。
  曖昧な候補を自動確定せず、候補ボタンまたは未解決表示にする。
  既存Q&Aの周辺本文取得も、未確認indexを使わず最新versionの一致を確認する。

## 3. Migration

`002_pdf_evidence_geometry.sql` / version=2 / name=`pdf_evidence_geometry`。
追加列はnullable TEXTの `bbox_json` と `role` のみ。

旧schema・v1登録済みDBとも自動backup後に移行する。既存のblock ID、Q&A、Knowledge、
Translation、highlight、Anchor JSONは変更しない。既存行のbbox/roleはNULLのまま残す。
全未適用migrationのtransaction・rollback・backup検証はPhase 0 runnerを継続利用する。
詳細: [移行・復旧](../../development/db_migrations.md)。

## 4. bboxデータ形式

```json
{
  "coordinate_system": "pymupdf_unrotated",
  "units": "pt",
  "rect": [40.0, 145.0, 432.721, 192.61],
  "page_rect": [0.0, 0.0, 595.0, 842.0],
  "rotation": 0,
  "spans": [
    {"text": "Synthetic evidence", "rect": [40.0, 145.0, 130.0, 160.0]}
  ]
}
```

ページは1開始。rectは `[x0,y0,x1,y1]`、CropBox左上原点・未回転PyMuPDF座標、単位point。
PDF保存座標は小数3桁に丸める。本文blockのrectはspan矩形のunion。
図表候補のspansは空配列。Anchorは同じgeometry形式を使い、spansを省略できる。
PDF.js viewportで回転・scaleを変換し、PDF選択はDOM座標から逆変換する。

## 5. 2段組の読み順

本文のx端点間にページ幅の2.5%以上の隙間があり、ページ幅の25〜75%に入るものを調べる。
左右に各2ブロック以上ある隙間から、左右に分類できる本文数、隙間幅の順でgutterを選ぶ。
全幅ブロックと他の本文と縦位置が重ならない見出しを帯の境界とし、各帯で左列を上から下、
続いて右列を上から下へ読む。gutterの根拠が足りない場合はy→x順に戻す。
PDF描画コマンドの挿入順には依存しない。3列以上・複雑な入れ子組版の一般解ではない。

## 6. Anchor解決

優先度: sourceVersion + blockId → page + bbox → quote/prefix/suffix → blockIdx → page →
候補 / unresolved。安全条件の詳細は [SourceAnchor正本](../../architecture/source_anchor_spec.md)。

bboxは小さい矩形の80%以上の重なりを候補条件にする。候補の一意性を確認し、
異なるversion/hashでは、引用と文脈が文書全体でも一意な場合に限り確定する。
引用全文を使い、重複・前後文脈の矛盾をindexで押し切らない。
同一versionのindex以外は位置情報として信頼しない。page_onlyは根拠未解決と表示する。
候補を手動で開いても保存済みAnchorやverificationは自動変更しない。

安定IDはSHA-256（version + kind/text/page/role/bbox + 同一内容の出現回数）。idxと
heading_pathを含めず、読み順の変更で同じEvidenceのIDが変わらないようにする。
異なるversionや内容・座標変更時は新IDになり、Anchor fallbackを使う。

## 7. Generated fixtures

Phase 0のsimple / two_column / visual_evidenceを継続し、以下を追加した。

- column_bands: 全幅見出しで上下の2段組を分ける。
- duplicate_evidence: 同じ引用が前後文脈の違う2箇所にある。
- image_evidence: PyMuPDFで生成したラスタ画像とcaption。
- shift / rotation / crop引数: 本文の位置変更、90度回転、CropBoxを再現する。

Vector図の領域とcaption、13行tableの領域とcaption、数式を独立したbboxで確認。
バイナリはGitに保存しない。[fixture仕様](../../../tests/fixtures/README.md)。

## 8. pytest結果

変更前: **21 passed, 1 warning**。
最終: **46 passed, 1 warning**（Python 3.12 / Node.js 24.19.0）。

```bash
.venv/bin/python -m pytest tests/ -q
```

Node.js 18+は実際のclient resolverをpytestから実行するための開発用要件。
アプリの起動・配布・buildには不要。Starlette TestClientのhttpx非推奨通知が1件残る。

1段組、2段組、帯境界、page、bbox/span妥当性、回転/CropBox、安定ID、bbox/quote fallback、
重複引用、曖昧なbbox、長い引用、未知位置、Q&A contextの安全性、新versionの再抽出と
ユーザーEvidence保全を検証した。旧schema/v1からv2への既存列の全行比較、backup復元、
冪等性、DDL/データ/履歴rollback、外部キー検証も成功。v1 SQL差分なし、diff --check成功。

## 9. Browser / API確認

Chromium 151.0.7922.173 + Playwright、1440×1000、mock、
専用 `/tmp/kg-phase1-browser-data` でgenerated PDFのみを使用した。

| 対象 | 結果 |
|---|---|
| PDF API登録・document取得・再取得 | geometryと安定IDを保存・返却 |
| PDF描画 | 2ページcanvas / text layerを表示 |
| 右列テキストの選択→highlight | 右列のblock ID・bbox・version/hashを保存 |
| 根拠ジャンプ | 対応する右列bboxに一時枠を表示 |
| block表示・リロード | 正しい引用のhighlightを復元 |
| block選択→mock Q&A | version/hash/bbox付きAnchorを保存 |
| bbox / quote fallback | Readerで成功 |
| 重複引用 | 候補2件を表示し、自動ジャンプ・領域枠なし |
| page fallback | ページのみの表示と未解決通知 |
| 回転 + CropBox | 選択座標の逆変換とbbox枠を確認 |
| browser例外 | pageerrorなし |
| Q&A / Translation / user memo / export / reopen | API回帰テスト成功 |

再現用のopt-inブラウザスクリプトを追加した。Playwrightはアプリ依存に加えない。
専用の使い捨てdataディレクトリでサーバーを起動して実行する。

```bash
.venv/bin/python -m pip install playwright
KG_DATA_DIR=/tmp/kg-phase1-browser-data KG_LLM_PROVIDER=mock \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8301
# 別terminalで（手元のChromiumのパスに合わせる）
.venv/bin/python scripts/smoke_phase1_browser.py --chromium /usr/bin/chromium
```

## 10. 既知の制約

- OCR、スキャンPDF、複雑な多段組、あらゆる図表/数式の意味分類は未対応。
  caption・数式判定と描画領域の結合はheuristicで、roleはあくまで候補。
- Captionとassetの確定関係、画像crop、asset永続化、Visual Clip/Paper Map UIはない。
- 旧blockに座標を自動補完しない。versionなしの裸のID/indexのみの旧Anchorは
  自動確定せず劣化する。通常の旧quote付きAnchorはfallbackで復元できる。
- 複数block/pageにまたがる選択に単一blockを割り当てない。PDF選択のbboxは表示文字の
  矩形に由来し、span bboxやblock bboxと完全一致するとは限らない。
- `/reanalyze` は既存のAI解析で、PDF再抽出APIではない。新抽出版のテストはDBで模擬する。
  PDF差替え機能や旧version原本を切り替えて表示する機能は未追加。
- Q&Aの周辺context安全確認は全文quoteが一意な場合のみの保守的なgate。
  bboxからのserver-side context解決、Context Builder v2、AI runtimeは未実装。
- 実論文・私有DB・Windowsでは未検証。実AIの品質はmockでは評価できない。

## 11. Phase 2への状態

**Phase 2のデータ設計・実装へ進める基盤は整った。** 新規PDFのversion/block/page/bboxと
保守的な根拠解決を、Paper Brief Evidence参照の入力として利用できる。
曖昧な候補や未解決をconfirmed根拠として扱わない契約を継続すること。
Phase 2以降のUI・schemaや、Phase 0AのCodex app-server / AI runtimeは実装していない。

## 12. PR作成前のレビュー項目

- bboxの単位・CropBox原点・未回転座標と、PDF.js変換の契約が将来のVisual Clipにも使えるか。
- gutter/帯境界のheuristicと図表/数式の候補判定を、確定した意味分類として扱っていないか。
- 同一versionのID/idxと、異なるversion/hashの可搬selectorの安全条件が適切か。
- 曖昧な引用・bboxを確定せず、page_only・旧Anchorの劣化が表示と保存で区別されているか。
- v2は2列だけの追加でv1を変更せず、既存ユーザー情報を保持しているか。
- 安定IDの64桁化、既存の16桁IDの共存、Node.jsのテスト要件が外部連携の前提に合うか。
- server context gateとclient resolverの責務差、およびPDF再抽出APIを追加していない範囲が明確か。

上記を実装・テスト・diffで自己レビューした。GitHub PRでは同じ点を重点的に確認する。
