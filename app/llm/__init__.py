"""Legacy complete() boundary, backed by subscription-first AIRuntime.

API provider classes are retained as future extensions, never selected by MVP.
"""
from ..config import runtime_config
from .base import LLMProvider, LLMResult
from .mock import MockProvider


def get_provider(runtime=None) -> LLMProvider:
    if runtime is None and runtime_config()["type"] == "mock":
        return MockProvider()
    from ..ai.compat import RuntimeProvider
    from ..ai.offline import NoAIRuntime
    return RuntimeProvider(runtime if runtime is not None else NoAIRuntime())


__all__ = ["get_provider", "LLMProvider", "LLMResult"]
