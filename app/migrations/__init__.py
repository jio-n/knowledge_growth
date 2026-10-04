"""Ordered SQLite migrations. Never use executescript inside a migration transaction."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


BASELINE_SCHEMA = (Path(__file__).parent / "001_baseline.sql").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


# Append only: later phases add versions here, without changing the v1 snapshot.
MIGRATIONS = (
    Migration(1, "july_2026_baseline", BASELINE_SCHEMA),
    Migration(2, "pdf_evidence_geometry", (Path(__file__).parent / "002_pdf_evidence_geometry.sql").read_text(encoding="utf-8")),
    Migration(3, "paper_brief_import", (Path(__file__).parent / "003_paper_brief_import.sql").read_text(encoding="utf-8")),
    Migration(4, "paper_brief_generation", (Path(__file__).parent / "004_paper_brief_generation.sql").read_text(encoding="utf-8")),
)


class MigrationError(RuntimeError):
    """The database cannot be safely migrated by this application version."""


def _execute_sql(con: sqlite3.Connection, sql: str) -> None:
    statement = ""
    for char in sql:
        statement += char
        if char == ";" and sqlite3.complete_statement(statement):
            con.execute(statement)
            statement = ""
    if statement.strip():
        # sqlite accepts trailing comments and a final statement without a semicolon.
        con.execute(statement)


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _validate_legacy(con: sqlite3.Connection) -> None:
    """Adopt only a complete v1 database; never stamp a partial/incompatible schema."""
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(BASELINE_SCHEMA)
        for table in _tables(expected):
            for pragma in ("table_info", "foreign_key_list"):
                want = [tuple(r) for r in expected.execute(f'PRAGMA {pragma}("{table}")')]
                got = [tuple(r) for r in con.execute(f'PRAGMA {pragma}("{table}")')]
                if got != want:
                    raise MigrationError(f"Legacy schema mismatch: {table} ({pragma})")
            indexes = {r[1]: tuple(r)[1:] for r in con.execute(f'PRAGMA index_list("{table}")')}
            for row in expected.execute(f'PRAGMA index_list("{table}")'):
                name = row[1]
                if name not in indexes and not row[2]:
                    continue  # v1 can safely recreate a missing non-unique index.
                want = [tuple(r) for r in expected.execute(f'PRAGMA index_xinfo("{name}")')]
                got = [tuple(r) for r in con.execute(f'PRAGMA index_xinfo("{name}")')]
                if indexes.get(name) != tuple(row)[1:] or got != want:
                    raise MigrationError(f"Legacy schema mismatch: {table} (index {name})")


def _backup(con: sqlite3.Connection, version: int) -> Path | None:
    """Use SQLite backup on a separate reader to include committed WAL data."""
    db_file = next(r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main")
    if not db_file:  # In-memory tests have no persistent file to back up.
        return None
    path = Path(db_file)
    directory = path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"{path.stem}.v{version}.{stamp}.{uuid.uuid4().hex[:8]}.db"
    try:
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("Migration backup failed integrity_check")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def run_migrations(con: sqlite3.Connection, *,
                   migrations: tuple[Migration, ...] = MIGRATIONS) -> Path | None:
    """Apply all pending versions atomically; return the pre-migration backup path.

    The caller owns/closes the connection. Foreign keys must be enabled. Persistent
    nonempty databases are backed up automatically; fresh databases need no backup.
    Downgrades and unknown/gapped histories are refused without modifying the DB.
    """
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise MigrationError("Migrations must have consecutive versions starting at 1")
    if con.in_transaction:
        raise MigrationError("Migrations require a connection without an active transaction")
    if not con.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise MigrationError("Migrations require PRAGMA foreign_keys=ON")

    con.execute("BEGIN IMMEDIATE")
    try:
        tables = _tables(con)
        history = []
        if "schema_version" in tables:
            history = [tuple(r) for r in con.execute(
                "SELECT version, name FROM schema_version ORDER BY version"
            )]
        expected = [(m.version, m.name) for m in migrations]
        if history != expected[:len(history)]:
            raise MigrationError("Unknown, newer, or incomplete migration history; use a matching app version")
        pending = migrations[len(history):]
        if not pending:
            con.commit()
            return None
        if not history and tables - {"schema_version"}:
            _validate_legacy(con)
        backup = _backup(con, len(history)) if tables else None

        con.execute("""CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )""")
        for migration in pending:
            _execute_sql(con, migration.sql)
            con.execute("INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                        (migration.version, migration.name,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")))
        violations = con.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError("Foreign key violations found; migration rolled back")
        con.commit()
        return backup
    except Exception:
        con.rollback()
        raise
