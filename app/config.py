"""Application configuration loader.

All tunables live in config/app.config.json so that other developers /
AI agents can change LLM providers, context limits and the note template
without touching code (see docs/architecture/system_architecture.md).

Env overrides:
  KG_DATA_DIR      — data directory (default <repo>/data). Used by tests.
  KG_AI_RUNTIME    — codex_chatgpt_plan / mock / no_ai.
  KG_CODEX_EXECUTABLE — native Codex binary path.
  KG_LLM_PROVIDER  — legacy mock override for tests / CI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "app.config.json"
PROMPTS_DIR = ROOT / "prompts"
CLIENT_DIR = ROOT / "client"


def _data_dir() -> Path:
    return Path(os.environ.get("KG_DATA_DIR", ROOT / "data"))


# NOTE: read via functions (not constants) so KG_DATA_DIR set by tests
# after import still takes effect.
def data_dir() -> Path:
    return _data_dir()


def files_dir() -> Path:
    return _data_dir() / "files"


def db_path() -> Path:
    return _data_dir() / "knowledge.db"


class _PathProxy:
    """Backwards-compatible module attributes DATA_DIR/FILES_DIR/DB_PATH that
    resolve lazily (so tests can set KG_DATA_DIR before first use)."""

    def __init__(self, fn):
        self._fn = fn

    def __getattr__(self, name):
        return getattr(self._fn(), name)

    def __fspath__(self):
        return str(self._fn())

    def __truediv__(self, other):
        return self._fn() / other

    def __str__(self):
        return str(self._fn())


DATA_DIR = _PathProxy(data_dir)
FILES_DIR = _PathProxy(files_dir)
DB_PATH = _PathProxy(db_path)

_config_cache: dict | None = None


def load_config(force: bool = False) -> dict:
    global _config_cache
    if _config_cache is None or force:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def runtime_config() -> dict:
    cfg = load_config().get("runtime", {"type": "codex_chatgpt_plan"})
    # Keep old mock test/dev switch. No legacy API selection can activate billing.
    kind = os.environ.get("KG_AI_RUNTIME")
    if not kind and os.environ.get("KG_LLM_PROVIDER") == "mock":
        kind = "mock"
    return {**cfg, "type": kind or cfg["type"],
            "executable": os.environ.get("KG_CODEX_EXECUTABLE", cfg.get("executable", "codex"))}


def llm_config() -> dict:
    # Compatibility metadata for legacy heuristic code; provider selection belongs
    # to AIRuntime. Legacy metered environment overrides never select API billing.
    cfg = load_config().get("llm", {})
    provider = "mock" if runtime_config()["type"] == "mock" else "runtime"
    return {**cfg, "provider": provider}


def note_template() -> list[dict]:
    return load_config()["note_template"]


def context_config() -> dict:
    return load_config()["context"]


def ensure_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    files_dir().mkdir(parents=True, exist_ok=True)
