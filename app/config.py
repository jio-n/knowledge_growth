"""Application configuration loader.

All tunables live in config/app.config.json so that other developers /
AI agents can change LLM providers, context limits and the note template
without touching code (see docs/architecture/system_architecture.md).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "app.config.json"
DATA_DIR = ROOT / "data"
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "knowledge.db"
PROMPTS_DIR = ROOT / "prompts"
CLIENT_DIR = ROOT / "client"

_config_cache: dict | None = None


def load_config(force: bool = False) -> dict:
    global _config_cache
    if _config_cache is None or force:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def llm_config() -> dict:
    cfg = load_config()["llm"]
    # environment variable overrides provider selection for tests / CI
    provider = os.environ.get("KG_LLM_PROVIDER", cfg.get("provider", "mock"))
    return {**cfg, "provider": provider}


def note_template() -> list[dict]:
    return load_config()["note_template"]


def context_config() -> dict:
    return load_config()["context"]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    FILES_DIR.mkdir(exist_ok=True)
