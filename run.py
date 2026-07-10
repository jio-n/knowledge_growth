"""Entry point: `python run.py` -> http://localhost:8300 (CLAUDE.md 起動手順)."""
from __future__ import annotations

import uvicorn

from app.config import ensure_dirs
from app.db import init_db

HOST = "127.0.0.1"
PORT = 8300


def main() -> None:
    ensure_dirs()
    init_db()
    print(f"http://{HOST}:{PORT}")
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
