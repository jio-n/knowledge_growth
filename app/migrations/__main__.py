"""Inspect or migrate KG_DATA_DIR/knowledge.db without starting the web server."""
import argparse
import sqlite3

from app.config import db_path
from app.db import get_db
from . import run_migrations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="read version history without migrating")
    args = parser.parse_args()
    if args.status:
        path = db_path()
        if not path.exists():
            print("Database does not exist")
            return
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    else:
        con = get_db()
    try:
        if not args.status:
            backup = run_migrations(con)
            if backup:
                print(f"Backup: {backup}")
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if not exists:
            print("Unversioned database")
        else:
            for row in con.execute("SELECT version, name, applied_at FROM schema_version ORDER BY version"):
                print(f"v{row[0]} {row[1]} (applied_at={row[2]})")
    finally:
        con.close()


if __name__ == "__main__":
    main()
