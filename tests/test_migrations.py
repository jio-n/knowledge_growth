"""Migration acceptance: adoption, data/provenance preservation, backup and rollback."""
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from app.db import init_db
from app.migrations import BASELINE_SCHEMA, MIGRATIONS, Migration, MigrationError, run_migrations


@pytest.fixture
def database(tmp_path):
    with closing(sqlite3.connect(tmp_path / "knowledge.db")) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        yield con


def _seed_legacy(con):
    con.executescript(BASELINE_SCHEMA)
    anchor = json.dumps({"type": "text-quote", "blockId": "b", "blockIdx": 0,
                         "page": 1, "quote": "Synthetic text", "prefix": "", "suffix": "."})
    con.execute("""INSERT INTO sources (id,type,title,file_path,reading_status,created_at,updated_at)
                   VALUES ('s','pdf','Synthetic source','synthetic.pdf','reading','old','old')""")
    con.execute("INSERT INTO source_versions (id,source_id,fetched_at,raw_path) VALUES ('v','s','old','synthetic.pdf')")
    con.execute("""INSERT INTO document_blocks (id,version_id,source_id,idx,kind,text,page,heading_path)
                   VALUES ('b','v','s',0,'para','Synthetic text.',1,'1 Method')""")
    con.execute("INSERT INTO tags VALUES ('tag','synthetic')")
    con.execute("INSERT INTO source_tags VALUES ('s','tag')")
    con.execute("""INSERT INTO questions (id,source_id,version_id,anchor,question_text,created_at)
                   VALUES ('q','s','v',?,'Why?','old')""", (anchor,))
    con.execute("""INSERT INTO answers (id,question_id,content,provider,model,context_summary,created_at)
                   VALUES ('a','q','Mock answer','mock','mock','{"blockIds":["b"]}','old')""")
    for origin in ("user", "llm_edited", "auto_extract"):
        con.execute("""INSERT INTO knowledge_items
                       (id,source_id,section_key,content,origin,info_type,verification,anchor,question_id,
                        answer_id,sort_order,created_at,updated_at)
                       VALUES (?,'s','method','User-preserved content',?,'user_thought','verified',?,
                               'q','a',3.5,'old','old')""", (origin, origin, anchor))
    con.execute("INSERT INTO highlights (id,source_id,version_id,anchor,comment,created_at) VALUES ('h','s','v',?,'Keep','old')", (anchor,))
    con.execute("""INSERT INTO translations (id,source_id,version_id,block_id,source_text,translated_text,
                   provider,model,user_edited,updated_at)
                   VALUES ('t','s','v','b','Synthetic text.','ユーザー修正済み訳','mock','mock',1,'old')""")
    con.commit()


def _snapshot(con):
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_version' ORDER BY name"
    )]
    # Snapshot legacy data independently of newly added, initially empty tables.
    with closing(sqlite3.connect(":memory:")) as baseline:
        baseline.executescript(BASELINE_SCHEMA)
        baseline_tables = {r[0] for r in baseline.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {table: [r[1] for r in baseline.execute(f'PRAGMA table_info("{table}")')]
                   for table in tables if table in baseline_tables}
    return {table: con.execute('SELECT ' + ','.join(columns.get(table, ["*"])) +
                              f' FROM "{table}" ORDER BY rowid').fetchall() for table in tables if table in columns or table == "test_extension"}


def test_fresh_database_and_idempotence(database, tmp_path):
    assert run_migrations(database) is None
    history = database.execute("SELECT * FROM schema_version").fetchall()
    assert [(r[0], r[1]) for r in history] == [(m.version, m.name) for m in MIGRATIONS]
    assert len(_snapshot(database)) == 10
    assert run_migrations(database) is None
    assert database.execute("SELECT * FROM schema_version").fetchall() == history
    assert not (tmp_path / "backups").exists()


def test_adopt_legacy_preserves_every_row_and_wal_backup(database, tmp_path):
    _seed_legacy(database)
    before = _snapshot(database)
    backup = run_migrations(database)
    assert backup and backup.exists()
    assert _snapshot(database) == before
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    with closing(sqlite3.connect(backup)) as restored:
        assert _snapshot(restored) == before
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone() is None
        # The backup can itself be adopted, validating the documented restore path.
        restored.execute("PRAGMA foreign_keys=ON")
        run_migrations(restored)
        assert _snapshot(restored) == before
    history = database.execute("SELECT * FROM schema_version").fetchall()
    assert run_migrations(database) is None
    assert database.execute("SELECT * FROM schema_version").fetchall() == history
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_future_versions_apply_in_order(database):
    _seed_legacy(database)
    before = _snapshot(database)
    run_migrations(database)
    future = MIGRATIONS + (
        Migration(4, "test_extension", "CREATE TABLE test_extension (id TEXT PRIMARY KEY);"),
        Migration(5, "test_fill", "INSERT INTO test_extension VALUES ('kept');"),
    )
    assert run_migrations(database, migrations=future).exists()
    assert database.execute("SELECT version FROM schema_version ORDER BY version").fetchall() == [(1,), (2,), (3,), (4,), (5,)]
    assert database.execute("SELECT * FROM test_extension").fetchall() == [("kept",)]
    assert {k: v for k, v in _snapshot(database).items() if k != "test_extension"} == before
    assert run_migrations(database, migrations=future) is None


def test_failure_rolls_back_ddl_data_and_entire_pending_history(database):
    _seed_legacy(database)
    before = _snapshot(database)
    future = MIGRATIONS + (
        Migration(4, "test_change", "CREATE TABLE test_extension (id TEXT); UPDATE knowledge_items SET content='changed';"),
        Migration(5, "test_failure", "INSERT INTO nonexistent_table VALUES (1);"),
    )
    with pytest.raises(sqlite3.OperationalError):
        run_migrations(database, migrations=future)
    assert _snapshot(database) == before
    assert database.execute("SELECT name FROM sqlite_master WHERE name IN ('schema_version','test_extension')").fetchall() == []
    run_migrations(database)  # A failed attempt does not prevent the next startup.


@pytest.mark.parametrize("history", [((2, "future"),), ((1, "renamed"),),
                                   ((1, "july_2026_baseline"), (3, "gap"))])
def test_refuse_unknown_or_incomplete_history(database, history):
    run_migrations(database)
    database.execute("DELETE FROM schema_version")
    database.executemany("INSERT INTO schema_version VALUES (?,?,'old')", history)
    database.commit()
    with pytest.raises(MigrationError, match="history"):
        run_migrations(database)
    assert database.execute("SELECT version,name FROM schema_version ORDER BY version").fetchall() == list(history)


def test_refuse_partial_legacy_database_without_stamping(database):
    database.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, title TEXT)")
    with pytest.raises(MigrationError, match="Legacy schema mismatch"):
        run_migrations(database)
    assert database.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone() is None


def test_refuse_legacy_missing_unique_constraint(database):
    database.executescript(BASELINE_SCHEMA.replace("name TEXT UNIQUE NOT NULL", "name TEXT NOT NULL"))
    with pytest.raises(MigrationError, match="index"):
        run_migrations(database)
    assert database.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone() is None


def test_foreign_key_violation_rolls_back_adoption(database):
    _seed_legacy(database)
    database.execute("PRAGMA foreign_keys=OFF")
    database.execute("UPDATE translations SET source_id='missing'")
    database.commit()
    database.execute("PRAGMA foreign_keys=ON")
    before = _snapshot(database)
    with pytest.raises(MigrationError, match="Foreign key violations"):
        run_migrations(database)
    assert _snapshot(database) == before
    assert database.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone() is None


def test_backup_failure_prevents_any_migration(database, tmp_path):
    _seed_legacy(database)
    before = _snapshot(database)
    (tmp_path / "backups").write_text("Cannot create a directory here")
    with pytest.raises(FileExistsError):
        run_migrations(database)
    assert _snapshot(database) == before
    assert database.execute("SELECT name FROM sqlite_master WHERE name='schema_version'").fetchone() is None


def test_startup_adopts_existing_database(tmp_path, monkeypatch):
    monkeypatch.setenv("KG_DATA_DIR", str(tmp_path))
    with closing(sqlite3.connect(tmp_path / "knowledge.db")) as legacy:
        _seed_legacy(legacy)
        before = _snapshot(legacy)
    init_db()
    init_db()
    with closing(sqlite3.connect(tmp_path / "knowledge.db")) as con:
        assert _snapshot(con) == before
        assert con.execute("SELECT version FROM schema_version").fetchall() == [(1,), (2,), (3,)]


def test_cli_status_and_migration(tmp_path):
    directory = tmp_path / "data"
    env = {**os.environ, "KG_DATA_DIR": str(directory)}
    command = [sys.executable, "-m", "app.migrations"]
    status = subprocess.run(command + ["--status"], env=env, capture_output=True, text=True, check=True)
    assert "does not exist" in status.stdout
    assert not directory.exists()
    subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    status = subprocess.run(command + ["--status"], env=env, capture_output=True, text=True, check=True)
    assert "v1 july_2026_baseline" in status.stdout
    assert "v2 pdf_evidence_geometry" in status.stdout
    assert "v3 paper_brief_import" in status.stdout


def test_stamped_v1_to_v2_preserves_legacy_and_unknown_geometry(database):
    _seed_legacy(database)
    run_migrations(database, migrations=MIGRATIONS[:1])
    before = _snapshot(database)
    backup = run_migrations(database, migrations=MIGRATIONS[:2])
    assert backup.exists() and _snapshot(database) == before
    assert database.execute("SELECT bbox_json,role FROM document_blocks").fetchall() == [(None, None)]
    with closing(sqlite3.connect(backup)) as restored:
        assert restored.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        assert _snapshot(restored) == before


def test_v2_to_v3_backup_preserves_all_existing_rows_and_anchor(database):
    _seed_legacy(database)
    run_migrations(database, migrations=MIGRATIONS[:2])
    before = _snapshot(database)
    database.execute("UPDATE document_blocks SET bbox_json='generated-bbox',role='para'")
    database.commit()
    backup = run_migrations(database)
    assert backup.exists()
    assert _snapshot(database) == before
    assert database.execute('SELECT bbox_json,role FROM document_blocks').fetchall() == [('generated-bbox', 'para')]
    assert database.execute('SELECT version FROM schema_version').fetchall() == [(1,), (2,), (3,)]
    for table in ('paper_briefs', 'paper_brief_fields', 'import_packages', 'import_previews'):
        assert database.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    with closing(sqlite3.connect(backup)) as restored:
        assert restored.execute('SELECT version FROM schema_version').fetchall() == [(1,), (2,)]
        assert restored.execute("SELECT name FROM sqlite_master WHERE name='paper_briefs'").fetchone() is None
        assert restored.execute('SELECT bbox_json,role FROM document_blocks').fetchall() == [('generated-bbox', 'para')]
        assert _snapshot(restored) == before
        restored.execute('PRAGMA foreign_keys=ON')
        run_migrations(restored)
        assert _snapshot(restored) == before


def test_v3_pending_failure_rolls_back_extension_and_preserves_v2(database):
    _seed_legacy(database)
    run_migrations(database, migrations=MIGRATIONS[:2])
    before = _snapshot(database)
    failing = MIGRATIONS + (Migration(4, 'injected_failure', "UPDATE knowledge_items SET content='lost'; INSERT INTO missing_table VALUES (1);"),)
    with pytest.raises(sqlite3.OperationalError):
        run_migrations(database, migrations=failing)
    assert _snapshot(database) == before
    assert database.execute('SELECT version FROM schema_version').fetchall() == [(1,), (2,)]
    assert database.execute("SELECT name FROM sqlite_master WHERE name LIKE 'paper_brief%' OR name LIKE 'import_%'").fetchall() == []
    run_migrations(database)
    assert _snapshot(database) == before
