"""FastAPI application factory (system_architecture.md).

Wires the DB, all `/api` routers and the static client build. Route
modules stay import-order independent — this file only assembles them.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import CLIENT_DIR, llm_config, note_template
from .db import init_db
from . import routes_export, routes_knowledge, routes_qa, routes_sources

PROMPT_TYPES = [
    {"key": "explain", "label": "分かりやすく説明"},
    {"key": "explain_simple", "label": "初学者向けに説明"},
    {"key": "detail", "label": "専門的に詳しく"},
    {"key": "critique", "label": "批判的に検討"},
    {"key": "apply", "label": "応用を考える"},
    {"key": "math", "label": "数式を分解"},
    {"key": "free", "label": "自由質問"},
]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Research Reading Workspace", lifespan=_lifespan)

    app.include_router(routes_sources.router)
    app.include_router(routes_qa.router)
    app.include_router(routes_knowledge.router)
    app.include_router(routes_export.router)

    @app.get("/api/meta")
    def get_meta():
        # NOTE: reads config directly instead of get_provider() so this endpoint
        # never raises when an API key is missing (provider is only instantiated
        # lazily, on first actual LLM call).
        cfg = llm_config()
        provider = cfg.get("provider", "mock")
        model = cfg.get(provider, {}).get("model", provider)
        return {"provider": provider, "model": model, "note_template": note_template(),
                "prompt_types": PROMPT_TYPES}

    # Static client build (client/ — may still be under construction; StaticFiles
    # only 404s on missing individual files, so this is safe to mount early).
    app.mount("/", StaticFiles(directory=str(CLIENT_DIR), html=True), name="client")
    return app


app = create_app()
