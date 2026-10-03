# Paper Brief persistence / Import Bridge backend foundation

更新: 2026-10-04。基点はmainのPhase 1 merge commit
`a80aee05c0773c3dbb37a448cf76b978270b0e6c`。

## 実装範囲とTask ID

| Task | この実装 |
|---|---|
| T2-01 | Core / AI拡張の厳密なPaper Brief JSON契約、専用DB保存・取得 |
| T2-02（保存部分） | paper_type enum。AI分類・表示順変更は未実装 |
| T2-04 | 未知field/status/versionを拒否。根拠なしconfirmed数値を拒否。未解決根拠をuncertain化 |
| T2-06（保存・保護部分） | schema/provider/model/prompt metadata保存、ユーザー修正保護。AI再生成は未実装 |
| T2A-01 | bounded ZIP、manifest/Brief/Evidence、任意Note/Q&A候補の検証 |
| T2A-02 | hash→DOI→arXiv→title/authors/year→title→手動選択 |
| T2A-03 | Phase 1契約のPython resolver、可搬selectorからローカルAnchorへ再解決 |
| T2A-04（backend部分） | field差分、根拠状態、競合、候補、field除外のJSON preview。フルUIは未実装 |
| T2A-05 | import provenance、user origin/user_edited保護、verification自動昇格なし |
| T2A-06 | validate / preview / commit API、CLI、transaction、rollback、二重import防止 |
| T2A-07 | JSON template、generator CLI/script、machine-readable schema、生成fixture |

AI runtime/app-server/OAuth、実AI抽出、Japanese Reader、Compare、Q&A改修、Visual Clipは含まない。
任意Note/Q&A payloadは候補のpreviewとpackage履歴保存までで、既存テーブルに反映しない。

## DB schema v3

`003_paper_brief_import.sql`を既存runnerの末尾に追加。v1/v2 SQLは変更しない。
既存sources/version/blocks/Q&A/knowledge/highlight/translationを変更・削除しない。

| table | 保存内容 |
|---|---|
| paper_briefs | source_id（PK/FK）、schema_version、revision、created_at、updated_at |
| paper_brief_fields | (source_id, field_name) PK、value_json、status、evidence_json、origin、user_edited、provenance_json、verification、updated_at |
| import_packages | package_id PK、payload_hash UNIQUE、source_id FK、manifest_json、payload_json、preview_json、imported_at |
| import_previews | ランダムpreview_id PK、検証済payload、選択/除外options、preview snapshot、作成/期限/commit日時 |

evidence_jsonは`{refs: [evidence_id], resolutions: [...]}`。field自身とKey Resultの根拠を
同じfieldのresolutionsに保持する。各resolutionにはportable selector、状態、候補、local Anchorが入る。
Key Resultのvalue_jsonはdataset/task/setting/metric/score/unit/split/comparison/status/evidenceを保持する。

provenance_jsonは入力のgenerator/model/provider/prompt_versionに加えてpackage_id、schema version、
generator label、import日時、入力origin/user_edited、要求status、元fieldを保持する。
確認操作はimportの承認であり、verificationは`unverified`のまま保存する。

## Paper Brief wire schema

保存契約は`paper-brief-0.1`。概念schemaのmodel/learning/architecture/evaluationグループを、
field単位の更新・保護ができるようにflat field名へ正規化した。
`app.paper_brief.PaperBrief.model_json_schema()`が機械可読な正本。
以前の概念JSONの裸文字列・配列やnested groupをそのまま受け入れない。

全fieldは同じenvelopeを持つ。

```json
{
  "value": "原文から要約した内容",
  "status": "derived",
  "evidence": ["ev-001"],
  "origin": "llm",
  "user_edited": false,
  "provenance": {
    "generated_by": "ChatGPT",
    "model": null,
    "provider": null,
    "prompt_version": null,
    "generated_at": null
  }
}
```

- 必須Core: paper_type / one_line_summary / research_objective / background / problem / target_task / target_domain。
- paper_type: method / benchmark / survey / dataset / analysis / system / position / other。
- status: confirmed / derived / uncertain / not_reported / not_applicable。
- 文: one_line_summary、objective、background、problem、proposed_method、architecture_summary、model_size、inference/training_requirements。
- 文字列配列: target_task/domain、inputs/outputs、model_family/base_model/backbones/modalities、trainable/frozen_parts、learning_regimes、supervision/adaptation_methods/pretraining、datasets/evaluation_settings/metrics/baselines、architecture_components/data_flow、important_figures/tables、novelty/limitations/failure_cases、suggested_reading_order/ablation。
- shots: 非負整数またはnull。
- reproducibility: code / weights / data / compute / license_notesのnullableな文字列。
- key_results: `id, dataset, task, setting, metric, score, unit, split, comparison, status, evidence`を持つ配列。
- 値が不明なら`value=null`と`not_reported`等を使う。任意fieldは省略できる。

Key Resultのdataset/task/setting/metric/unitは空でない文字列。split/comparisonはkey自体が必須で、
分からない場合nullを使う。有限のscoreのみ許可。confirmedでEvidence IDがない数値はpackage検証で拒否する。
Evidence IDがあってもローカルで未解決/曖昧ならpreview/commit時にuncertainへ下げる。
親fieldのEvidenceは、個別結果のEvidenceの代わりにはならない。

## .kgpack wire schema

正規のversion keyは依頼仕様に従い`kgpack_schema_version`。
概念specの`package_schema_version`はaliasとして受け入れず、安全に拒否する。
元PDF/assetはkgpack-0.1 backendで常に拒否する。ZIPをfilesystemへ展開しない。

必須memberは`manifest.json`, `paper_brief.json`, `evidence_refs.json`。
任意memberは`knowledge_items.json`, `qa_threads.json`。ほかのmember、directory、PDF、assetは拒否する。
manifest.payloadsはmanifest以外のmember一覧と厳密に一致する。

```json
{
  "kgpack_schema_version": "kgpack-0.1",
  "package_id": "unique-package-id",
  "generated_at": "2026-10-04T00:00:00Z",
  "generated_by": "ChatGPT",
  "source_identity": {
    "title": "Paper title",
    "authors": ["Author"],
    "year": 2026,
    "doi": null,
    "arxiv_id": null,
    "source_hash": null
  },
  "paper_brief_schema_version": "paper-brief-0.1",
  "provenance_notice": "AI-generated analysis; verify against source",
  "payloads": ["paper_brief.json", "evidence_refs.json"]
}
```

source_hashは既存source_versions.content_hashと同じSHA-256（PDF登録時の原本bytes）。
Unknown hashを推測して作らない。generated_atはtimezone付きISO8601。
Evidenceはevidence_id、page（1-based）、quote/prefix/suffix、heading、Figure/Table label、optional bbox/source_hash。
statusはportableのみ。bboxはPhase 1の未回転pt座標契約で、optional spansも検証する。
foreign block_idは受け入れるが、local IDとして使用しない。heading単独・bare IDでは確定しない。

任意Note候補はid/section_key/title/content/evidence/provenance、Q&A候補はid/title/question/answer/evidence/provenance。
origin/verificationの入力指定によるlocal権限昇格を認めない。

### Security validation

- package最大8 MiB、member最大2 MiB、展開合計最大6 MiB、圧縮比最大200。
- 実際の展開サイズ・圧縮stream終端も検証し、JSONの後ろに隠したPDFや偽のsize/CRCを拒否。
- ZIP Stored/Deflateのみ。暗号化、symlink/non-regular file、重複member、破損/CRC失敗を拒否。
- exact member allowlistでpath traversal/絶対パス/Windowsパスを拒否。
- prepend/trailing bytes、ZIP comment、orphan local recordを拒否し、未列挙PDFを隠すZIPを受け入れない。
- ストリームZIPの通常のdata descriptorは対応。ZIP64/自己展開/特殊ZIP形式は未対応。
- UTF-8 JSONのみ。重複JSON key、NaN/Infinity、必須key欠落、型違い、未知key/version/statusを拒否。
- Evidence/Result/Note/Q&A IDの同一namespace内重複とdangling refを拒否。
- 入力本文を含めない構造エラーを返す。HTML/markdown実行やAI/network呼出しは行わない。

## Import flow / API

1. `POST /api/import/kgpack/validate` — multipart file。PDF/DBなしのpackage検証。
2. `POST /api/import/kgpack/preview` — multipart file、optional source_id、exclude_fields（JSON配列string）。
3. source match、最新versionのEvidence resolve、差分/競合をJSONで返す。24時間有効のpreview_idを保存する。
4. userがtarget、変更、除外、uncertainへの降格を確認する。
5. `POST /api/import/kgpack/commit` — JSON `{ "preview_id": "...", "confirmed": true }`。

previewはsources/Brief/Note/Q&Aに書き込まない。staging recordだけ保存する。
preview responseにはsource_matching、target、fields/action/current/incoming、各根拠と結果のresolution、
resolved/candidates/page_only/unresolved件数、unresolved_count、ambiguous_count、conflicts、warnings、
can_commit、disposition（importable/requires_review）、review_reasonsを返す。
weak/ambiguous/unmatchedはsource_idで手動選択し直してfresh previewを作る。
exclude_fieldsを変更する場合もpreviewを作り直す。保護fieldは自動的にpreserve_user。

commitのconfirmedは対象選択とpreview内容の明示承認。requires_reviewの項目も承認後に保存できるが、
未解決根拠のstatusをconfirmedへ戻さない。対象未選択・二重import・書込対象なしはcommit不可。

- `GET /api/sources/{source_id}/paper-brief` — `{paper_brief: null | {schema_version, revision, fields, ...}}`。
  fieldはvalue/status/evidence IDs/evidence_resolution/origin/user_edited/provenance/verificationを返す。
- `PATCH /api/sources/{source_id}/paper-brief/fields/{field_name}` — 上記wire envelopeで明示的にユーザー編集。
  origin=user、user_edited=trueを付与する。既存fieldのEvidenceのみ再利用可。
  現行versionに再解決し、古いresolvedをそのままconfirmedの根拠として使用しない。

validate errorsは422、uploadサイズ超過は413、stale/duplicate/期限切れ/commit条件違反は409。
アプリの既存local APIと同じアクセスモデルで、認証やChatGPT接続を追加しない。

### Source matching algorithm

latest source_versionsを持つPDFのResearchSourceが対象。
content hash、DOI、arXiv、title+authors+year、titleの順で候補を探す。
DOI URL/prefix/case、arXiv URL/version、title/authorのNFKC/case/空白を正規化する。
同じ優先tierに複数候補があればambiguous。候補なしはunmatched。
一意のhash/DOI/arXivだけがstrongで自動的なpreview targetになる。
識別子が矛盾するcandidateは手動選択が必要。title/authors/yearの完全一致でもweakとして手動選択を要求する。
strong matchでもpreviewを省略したcommitはできない。

### Evidence algorithm

`app/source_anchor.py`はPhase 1のclient resolver契約をPythonで共有する。
portable入口でforeign block_id/version/indexを使わず、page/bbox/quote/context/label/hashで解決する。

- page+bbox: 同一ページ寸法、交差面積/小さい矩形面積≥0.8、一意候補が必要。
  同一hash（Evidenceまたはmanifest）か文書全体で一意のquote/contextが必要。
- quote+prefix/suffix: 全文正規化一致、全blockの全出現位置を数える。同一block内の重複も曖昧。
- label-only: Figure/Table captionの先頭labelを厳密一致し、一意の場合だけ解決する。
- heading/page単独は確定しない。pageは粗いnavigationのみ。quote文脈の矛盾は候補として返す。
- resolvedはlocal version/hash/block ID/index/page/bboxを持つSourceAnchorへ変換する。
  stale bboxよりも一意quoteを優先して再接続できる。候補/未解決には確定Anchorを付与しない。

### Conflict / transaction policy

BEGIN IMMEDIATE中で保存済payload/optionsを再検証し、source match/version/blocks/既存Briefのpreviewを再計算。
snapshotが違えば409で再確認を要求する。元DBを変更しない。
全field・package provenance・commit状態を同一transactionで保存し、例外時に全てrollback。
origin=userまたはuser_editedの既存fieldはSQL upsert側でも更新を拒否する。
外部packageがuser origin/user_editedを主張してもlocalではchatgpt_import/falseにし、入力宣言はprovenanceに残す。

package_idとpayload_hashの両方で二重importを検知する。hashはZIP圧縮・JSON key順・省略defaultの違いに
依存しないcanonical payloadで計算し、package_id/generated_atだけを変えた再送も検知する。
内容やsource metadata/generatorまで変更した成果物の意味的な重複検出は行わない。
source削除時は関連Brief/import履歴をcascade削除する。期限切れpreviewの自動GCは未実装。

## generated fixtures / 実行手順

すべて自作のgenerated PDFと架空内容。実論文PDF、APIキー、実AIは不要。
[package.template.json](examples/package.template.json)はcombined JSON（ZIP memberをkeyにしたgenerator入力）。

```bash
.venv/bin/python scripts/generate_kgpack_fixtures.py --output /tmp/kgpack-fixtures
.venv/bin/python -m app.import_bridge generate docs/redesign/v0.4/examples/package.template.json /tmp/sample.kgpack
.venv/bin/python -m app.import_bridge validate /tmp/sample.kgpack
.venv/bin/python -m pytest tests/ -q
```

scriptはgenerated.pdf、15種類のkgpack、template、Package/Paper Brief JSON schemaを出力する。
valid/wrong paper/DOI/arXiv/ambiguous title/unique quote/duplicate quote/missing evidence/stale bbox/
full-evidence result/no-evidence result/user conflict/duplicate import/malformed/unsafe ZIPを含む。
user-conflict/duplicate-importは同じpackageに対応するDB状態をテストで作って確認する。
fixture binaryはGitに保存しない。

## 既知の制約 / 次の作業

- Evidence resolveは一致箇所の再接続であり、主張や数値が引用内容から導けるかを実AIで検証しない。
  confirmedは申告statusと一意の根拠位置が揃った状態で、verificationとは独立する。
- OCR、複数block/pageにまたがるquote、fuzzy quote、複雑なFigure numberingは未対応。
- Previewやpackage履歴に全文block snapshotを保持するため、保持容量の最適化/GCは後続。
- Optional Note/Q&A反映、画像/PDF/asset、per-evidence候補の永続確定UIは未実装。
  Import確認UI・候補閲覧・Brief表示は後続の[T2A-04/T2-05](import_brief_ui.md)で実装済み。
- Runtime/app-server/OAuth/Japanese Reader/Compareはこのbranchで実装しない。
- T2A-04/T2-05の確認・表示UIは[実装記録](import_brief_ui.md)を参照。
  候補の永続確定と任意候補反映は後続。AI接続は別タスクT0A、Readerは別タスクT3として進める。

## 検証結果（2026-10-04）

- baseline: 46 passed（Phase 1 main）。
- final: `.venv/bin/python -m pytest tests/ -q` → **150 passed, 1 warning**。
  既存Starlette/httpx deprecation warningのみ。APIキー/実AI/実論文PDFは使用しない。
- CLI generate→validate: 成功、exit 0。unsafe package: `valid=false`、exit 1。
- Uvicorn + HTTPの実サーバー確認: generated PDF登録、validate 200、preview 200、
  Evidence resolved 2/candidates 0/page_only 0/unresolved 0、commit 200、Brief取得成功。
  同じpreviewの再commit 409、unsafe package 422。preview前後で未commit Briefはnull。
- migration: v1/v2 SQL差分なし。v2→v3 backup復元、DDL/data/history rollback、
  既存Q&A/Note/Translation/Anchor/geometryの保全を検証。
- Anchor: PythonとPhase 1 JavaScriptの8ケースで完全一致を確認。
- Security: traversal、未知payload/version/field/status、重複ID/ref/key/member、
  oversized/compression ratio、暗号化/symlink/破損、PDF/asset、hidden/orphan/trailing dataを検証。

## 変更ファイル

| 区分 | ファイル |
|---|---|
| Paper Brief schema / persistence | `app/paper_brief.py`, `app/brief_store.py`, `requirements.txt`（Pydantic v2契約を明示） |
| Anchor / API assembly | `app/source_anchor.py`, `app/routes_import.py`, `app/main.py` |
| Import backend / CLI | `app/import_bridge/__init__.py`, `__main__.py`, `schema.py`, `validator.py`, `matching.py`, `service.py` |
| Migration | `app/migrations/003_paper_brief_import.sql`, `app/migrations/__init__.py` |
| Generated fixtures / tests | `scripts/generate_kgpack_fixtures.py`, `tests/fixtures/kgpack_factory.py`, `tests/fixtures/README.md`, `tests/test_import_bridge.py`, `tests/test_migrations.py` |
| Architecture docs | `docs/architecture/data_model.md`, `api_spec.md`, `source_anchor_spec.md` |
| Development docs | `docs/development/db_migrations.md`, `testing.md` |
| v0.4 source-of-truth / implementation record | `docs/redesign/v0.4/README.md`, `knowledge_growth_mvp_spec_v0_4.md`, `knowledge_growth_implementation_tasks_v0_4.md`, `paper_brief_schema_v0_1.md`, `import_bridge_spec_v0_1.md`, `paper_brief_import_foundation.md`, `examples/package.template.json` |
