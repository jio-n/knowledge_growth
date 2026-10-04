# データモデル

最終更新: 2026-10-04 / schema v2。正本: `app/migrations/001_baseline.sql` と `002_pdf_evidence_geometry.sql`。v1は固定し、新しいmigrationで拡張する。

## ER 概要

```
sources 1--* source_versions 1--* document_blocks
sources 1--* questions 1--* answers
sources 1--* knowledge_items   (question_id/answer_id で Q&A に任意リンク)
sources 1--* highlights
sources 1--* translations      (block_id で document_blocks に任意リンク)
sources *--* tags              (source_tags)
```

## テーブル

### sources — 資料(ResearchSource, §3・§20)
共通上位概念。PDF固有情報(file_path)・論文固有情報(doi, arxiv_id)・Web固有情報(canonical_url, site_name)は同一テーブルのnullable列として拡張。

| 列 | 型 | 意味 |
|----|----|------|
| id | TEXT PK | uuid4 hex 16桁 |
| type | TEXT | `pdf` \| `web` \| `text` \| `markdown` |
| title, authors(JSON配列), year, venue | | 書誌 |
| url, canonical_url, doi, arxiv_id, site_name, published_at | | 識別子・出典 |
| lang | TEXT | 2文字言語コード(推定) |
| content_hash | TEXT | PDF=バイト列sha256 / web,text=抽出本文sha256。重複検出キー |
| file_path | TEXT | data/files/ 相対。PDF原本 or HTMLスナップショット |
| reading_status | TEXT | unread/reading/read/recheck |
| importance | INT | 0-3 |
| analysis_status | TEXT | pending/running/done/error/skipped(初期抽出の状態機械) |
| one_line_summary | TEXT | 初期抽出の一言要約(非正規化キャッシュ) |
| created_at, updated_at, last_opened_at | TEXT | ISO8601 UTC |

### source_versions — 取得版(§6)
Webページ再取得・PDF差替えに備え、抽出結果は必ず版に属する。MVPでは資料1件=版1件だが、再取得機能追加時にこのテーブルがそのまま受け皿になる。raw_path が取得時スナップショット。

### document_blocks — 抽出本文の構造単位
全資料タイプの共通正規形。**アンカー・コンテキスト構築・翻訳・表示はすべてブロック単位**(ADR-002)。

| 列 | 意味 |
|----|------|
| idx | 版内の順序(0開始)。アンカーの blockIdx に対応 |
| kind | heading/para/code/quote/list/table/figure |
| level | 見出しレベル |
| page | PDFのみ1開始ページ番号 |
| heading_path | "3 Method > 3.2 Loss" 形式の所属見出し階層 |
| bbox_json | nullable TEXT。未回転PyMuPDF座標・ページ寸法・rotation・span矩形を含むJSON |
| role | nullable TEXT。figure_caption/table_caption/figure_candidate/table_candidate/equation_candidate |

通常本文・見出しのroleはNULL。図表領域候補はkind=figure/table、textは空文字。
roleは候補識別であり、意味の確定・captionとassetの確定関係を表さない。
既存行は新列がNULLのまま残り、座標や分類を推測で補完しない。
source_hashはsource_versions.content_hashを再利用する。parent_block_id/asset_refは
Phase 1で使う実体がないため追加しない。bbox契約と読み順は
[Phase 1記録](../redesign/v0.4/phase1_pdf_anchor.md) を参照。

GET document APIではbbox_jsonに加え、パース済みのbboxを返す。

### questions / answers — 対話履歴(§10)
- questions.anchor: SourceAnchor JSON(source_anchor_spec.md)
- questions.prompt_type: explain/explain_simple/detail/critique/apply/math/free
- answers に provider, model, prompt_id, prompt_version, context_summary(送信コンテキストの記録JSON) を必ず保存 → 回答の再現性・監査性(§10)

### knowledge_items — 保存された知識(§11・§20)
理解ノートの実体。ノート=「note_template のセクション順に knowledge_items を並べたもの」であり、独立したノート本文テーブルは持たない(ADR-004)。

| 列 | 意味 |
|----|------|
| section_key | config/app.config.json の note_template キー(background, method, ... misc)。テンプレート外キーも許容(エクスポート時は末尾に出力) |
| origin | source_quote(原文引用)/ auto_extract(初期抽出)/ llm(AI回答由来)/ llm_edited(AI回答をユーザー編集)/ user(ユーザー記述) |
| info_type | requirements.md の語彙参照 |
| verification | unverified/verified/disputed(ユーザーが確認したか) |
| anchor | SourceAnchor JSON。原文へ戻るリンク(§14) |
| question_id, answer_id | 由来Q&Aへの逆リンク |
| sort_order | セクション内の並び(REAL、挿入は末尾+1) |

不変条件:
- `origin='auto_extract'` の項目のみ再解析で削除・再生成される。他のoriginは自動処理で変更禁止(§21)。
- `origin='llm'` の content をユーザーが編集したら `llm_edited` に遷移させる。

### highlights / translations / tags
- translations: block_id 単位でキャッシュ(同一ブロック再翻訳を防ぐ)。user_edited フラグでAI訳とユーザー修正を区別(§15)。
- highlights: anchor + color + comment。

## ID・時刻の規約
- 通常ID: `uuid.uuid4().hex[:16]`。既存IDは変更しない。
- 新規document_blocks.id: version + kind/text/page/role/bbox + 同一内容の出現回数を
  SHA-256で64桁にする。idx・heading_pathには依存せず、同一versionで同一抽出結果なら安定。
  異なるversionは異なるID。内容や座標が変わる場合はAnchor fallbackで確認する。
- 時刻: UTC ISO8601 秒精度。表示時にローカライズはクライアント側の責務。

## マイグレーション方針

`schema_version(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)`
に適用済みmigration履歴を保持する。July 2026 baselineをv1、PDF geometry追加をv2として登録する。
研究オブジェクトの既存列・ID・Anchor JSONは変更しない。

`app.db.init_db()` がmigration runnerを呼ぶ。未版管理の現行DBはschemaを検証し、
SQLite Backup APIでバックアップしてから登録する。全未適用migrationを1 transactionで
実行し、失敗時はDDL・データ・履歴をrollbackする。未知・新しい版は自動downgradeしない。

後続Phaseではv1 SQLを変更せず、連番migrationと本書の更新を追加する。
詳細な実行方法・backup・復旧手順は [db_migrations.md](../development/db_migrations.md) を参照。

## Paper Brief / Import Bridge（v3追加）

knowledge_itemsとは独立したpaper_briefs / paper_brief_fieldsへ構造化Briefを保存する。
import_previewsへ確認用snapshot、import_packagesへ二重import防止とprovenanceを保存する。
既存テーブルは変更しない。[schemaと保護方針](../redesign/v0.4/paper_brief_import_foundation.md#db-schema-v3)を参照。

## Paper Brief generation（v4追加）

paper_brief_generationsに生成状態・safe error・document/Brief digest・preview・時刻を保存する。
source_idはsourcesへのFKでcascade削除。state=generatingはsourceごとに一意。
previewは共有Paper Brief field形式のstagingで、accepted Briefの並行保存schemaではない。
確定時は既存paper_brief_fieldsとjob状態を同じtransactionで保存する。
[API・保護・復旧契約](../redesign/v0.4/structured_brief_generation.md)。
