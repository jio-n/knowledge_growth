"""LLM provider factory. Provider selection via config/app.config.json
(`llm.provider`) or env var KG_LLM_PROVIDER. See ADR-003."""
from __future__ import annotations

from ..config import llm_config
from .base import LLMProvider, LLMResult
from .mock import MockProvider


def get_provider() -> LLMProvider:
    cfg = llm_config()
    name = cfg["provider"]
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg["anthropic"], timeout=cfg.get("timeout_seconds", 120))
    if name == "openai_compat":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(cfg["openai_compat"], timeout=cfg.get("timeout_seconds", 120))
    return MockProvider()


__all__ = ["get_provider", "LLMProvider", "LLMResult"]
