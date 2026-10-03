#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN=python
fi

echo "[knowledge_growth] Codex Cloud setup"
"$PYTHON_BIN" scripts/bootstrap_dev.py --setup --test

echo
echo "[knowledge_growth] Cloud environment ready."
echo "Baseline command: .venv/bin/python -m pytest tests/ -q"
