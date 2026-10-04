# SQLite migration・バックアップ・復旧

更新: 2026-10-04 / redesign v0.4 Structured Brief generation (schema v4)

## 起動時の移行

FastAPI起動時の `app.db.init_db()` が `app.migrations.run_migrations()` を実行する。
`KG_DATA_DIR` 未指定なら対象は `data/knowledge.db`。

- 新規DB: `001_baseline.sql` の10テーブルとindexを作成し、v1/v2/v3/v4を登録する。
- 版管理のない現行DB: v1の列定義・外部キー・indexを検証し、バックアップ後にv1/v2/v3/v4を登録する。
  既存ID、SourceAnchorのJSON、ノート、origin、verification、翻訳修正フラグ等は変更しない。
- 移行済みDB: 未適用の版のみ順番に実行する。再起動で履歴やバックアップを増やさない。
- 未知の版、新しい版、履歴の欠落・名前の不一致、現行schemaと異なる未版管理DBは拒否する。
- 全未適用migrationと版登録を1つの `BEGIN IMMEDIATE` transaction内で実行し、最後に
  `foreign_key_check` を確認する。SQL・検証・バックアップの失敗時は移行を中止する。
  DBを削除して起動を通す処理はない。

`schema_version` はmigrationごとの履歴を保持する。

| 列 | 内容 |
|---|---|
| `version` | INTEGER PRIMARY KEY、1から連続する版 |
| `name` | migration識別名 |
| `applied_at` | UTC ISO8601 |

サーバーを起動せず、状態確認・移行を実行できる。

```bash
.venv/bin/python -m app.migrations --status
.venv/bin/python -m app.migrations
```

WindowsではPythonのパスを `.venv\Scripts\python.exe` に読み替える。
実際の私有DBをCloudへアップロードする必要はない。

## バックアップ

既存テーブルのある永続DBに未適用migrationがあれば、変更前にSQLite Backup APIで
`<DBの親>/backups/knowledge.v<移行前の版>.<UTC timestamp>.<suffix>.db` を作成する。
未版管理DBの版は `v0`。WAL内のコミット済みデータを含む独立したDBファイルになり、
`integrity_check` の成功を確認する。バックアップ不能なら移行を続けない。
失敗した移行でも完成したバックアップは残す。新規DBとメモリ内DBは自動backup対象外。

自動backupはSQLiteだけを対象とする。PDF等の `files/` とブラウザのlocalStorageは含まない。
運用上の復旧に備え、移行前にアプリ・他のDB利用プロセスを終了し、`data/` 全体も別の場所へ
コピーする。バックアップの自動削除は行わない。不要分の整理は利用者が行う。

## rollback / 復旧手順

SQL失敗時は未適用migration全体が自動rollbackされ、既存のデータと適用済み履歴が残る。
エラーを解消して再実行する。schemaが既に新しいアプリから古いアプリへdowngradeする
場合は、逆向きSQLではなくバックアップ復元を使う。

1. アプリ・すべてのSQLite接続を終了する。
2. 現在の `data/` 全体を退避する。移行後に作成したノート等は旧backupには含まれない。
3. 移行前のbackupを読み取り専用で開き、`PRAGMA integrity_check` が `ok` であることを確認する。
4. 停止状態を維持して `knowledge.db`、存在する `knowledge.db-wal` と `knowledge.db-shm` を
   同じ退避先へ移動する。古いWAL/SHMを復元したDBと混在させない。
5. 選択したbackupを `data/knowledge.db` にコピーする。対応する `files/` を維持・復元する。
6. backupの版に対応するコードで起動する。v0/v1 backupはJuly baselineと互換。
   Phase 0コードでv0 backupを開く場合は再度v1登録が行われる。
7. PDF、質問、ノート、翻訳、原文へのリンクを確認する。

## 次schemaの追加規約

`001_baseline.sql` は現行schemaの固定snapshotであり、後続Phaseで編集しない。
次のSQLファイルを追加し、`MIGRATIONS` に次の連番の `Migration(version, name, sql)` を追加する。
`app.db.SCHEMA` はv1互換aliasであり、最新schema生成はrunnerを使用する。

migration SQLに `BEGIN` / `COMMIT` / `ROLLBACK` / `VACUUM` や
`PRAGMA foreign_keys=OFF` を含めない。transactionはrunnerが所有する。
Python側で `executescript()` を使うと暗黙commitされるためrunnerでは使用しない。
データの書換えが必要な場合もユーザー編集・origin・SourceAnchorの保全を受入条件に含める。
既存DBからの移行、繰返し起動、失敗時のデータ・DDL・履歴rollbackをテストし、
`docs/architecture/data_model.md` を同時に更新する。

現時点の実schemaはv4。`002_pdf_evidence_geometry.sql`は固定したまま、
`003_paper_brief_import.sql`でpaper_briefs、paper_brief_fields、import_packages、import_previewsを追加する。
既存Q&A/Note/Translation/Anchor/geometryを移行で変更しない。
v2の永続DBはbackup後にv3へ移行する。v2 backupはPhase 1コードで復旧可能。
v2→v3のbackup復元、後続失敗時のDDL/data/history rollbackをgenerated DBでテストする。
詳細なschemaとimport transactionは[Import foundation](../redesign/v0.4/paper_brief_import_foundation.md)を参照。

`004_paper_brief_generation.sql`はjob lifecycleとpreview用paper_brief_generationsを追加する。
v1〜v3 SQLや既存Brief/user edit/SourceAnchorは変更しない。
v3→v4もbackup後に移行する。v3 backupでPR #5のアプリへ復旧できる。
[生成のtransaction・中断復旧](../redesign/v0.4/structured_brief_generation.md)。
