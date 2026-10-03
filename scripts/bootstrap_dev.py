#!/usr/bin/env python3
"""Cross-platform bootstrap helper for knowledge_growth.

Usage:
  python scripts/bootstrap_dev.py --setup
  python scripts/bootstrap_dev.py --setup --test
  python scripts/bootstrap_dev.py --launch

The launcher intentionally uses the repository-local .venv and never writes
credentials. It is safe to rerun: dependency/vendor setup is skipped while
the setup fingerprint is unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
MARKER = VENV / ".kg_bootstrap.json"
REQUIREMENTS = ROOT / "requirements.txt"
FETCH_VENDOR = ROOT / "scripts" / "fetch_vendor.py"
VENDOR_FILES = [
    ROOT / "client" / "vendor" / "pdf.mjs",
    ROOT / "client" / "vendor" / "pdf.worker.mjs",
    ROOT / "client" / "vendor" / "marked.esm.js",
]


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def check_host_python() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit(
            f"Python 3.11+ is required; found {sys.version.split()[0]}."
        )


def fingerprint() -> str:
    h = hashlib.sha256()
    for path in (REQUIREMENTS, FETCH_VENDOR):
        h.update(path.read_bytes())
    h.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    return h.hexdigest()


def read_marker() -> dict:
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check)


def ensure_environment() -> None:
    check_host_python()
    py = venv_python()

    if not py.exists():
        print("[knowledge_growth] Creating .venv ...")
        run([sys.executable, "-m", "venv", str(VENV)])

    current_fp = fingerprint()
    marker = read_marker()
    vendor_ok = all(p.exists() and p.stat().st_size > 1000 for p in VENDOR_FILES)

    if marker.get("fingerprint") != current_fp:
        print("[knowledge_growth] Installing/updating Python dependencies ...")
        run([str(py), "-m", "pip", "install", "-r", str(REQUIREMENTS)])
        vendor_ok = False

    if not vendor_ok:
        print("[knowledge_growth] Fetching pdf.js / marked.js ...")
        run([str(py), str(FETCH_VENDOR)])

    MARKER.write_text(
        json.dumps(
            {
                "fingerprint": current_fp,
                "python": sys.version.split()[0],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("[knowledge_growth] Environment is ready.")


def run_tests() -> None:
    run([str(venv_python()), "-m", "pytest", "tests/", "-q"])


def launch() -> int:
    ensure_environment()
    py = venv_python()
    print("[knowledge_growth] Starting http://127.0.0.1:8300")
    proc = subprocess.Popen([str(py), "run.py"], cwd=ROOT)
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8300")
    print("[knowledge_growth] Close this window or press Ctrl+C to stop.")
    try:
        return proc.wait()
    except KeyboardInterrupt:
        print("\n[knowledge_growth] Stopping ...")
        proc.terminate()
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            return proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true", help="prepare .venv/dependencies/vendor files")
    parser.add_argument("--test", action="store_true", help="run pytest after setup")
    parser.add_argument("--launch", action="store_true", help="prepare environment and start the app")
    args = parser.parse_args()

    if not (args.setup or args.test or args.launch):
        args.setup = True

    if args.launch:
        return launch()

    ensure_environment()
    if args.test:
        run_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
