"""FastAPI application factory (system_architecture.md).

Wires the DB, all `/api` routers and the static client build. Route
modules stay import-order independent — this file only assembles them.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import CLIENT_DIR, note_template
from .ai import create_runtime
from . import routes_ai
from .db import init_db
from . import routes_export, routes_import, routes_knowledge, routes_qa, routes_sources

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
    app.state.ai_runtime.start()
    try:
        yield
    finally:
        app.state.ai_runtime.close()


def create_app(runtime=None) -> FastAPI:
    app = FastAPI(title="Research Reading Workspace", lifespan=_lifespan)

    app.state.ai_runtime = runtime if runtime is not None else create_runtime()
    app.include_router(routes_ai.router)
    app.include_router(routes_sources.router)
    app.include_router(routes_qa.router)
    app.include_router(routes_knowledge.router)
    app.include_router(routes_export.router)
    app.include_router(routes_import.router)

    @app.get("/api/meta")
    def get_meta():
        runtime = app.state.ai_runtime
        provider = runtime.status().runtime
        model = "mock" if provider == "mock" else "runtime-default"
        return {"provider": provider, "model": model, "note_template": note_template(),
                "prompt_types": PROMPT_TYPES}

    # Static client build (client/ — may still be under construction; StaticFiles
    # only 404s on missing individual files, so this is safe to mount early).
    app.mount("/", StaticFiles(directory=str(CLIENT_DIR), html=True), name="client")
    return app


app = create_app()
