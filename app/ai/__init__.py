"""Runtime factory. Metered providers remain a future implementation boundary."""
from .base import AIRuntime, AIError
from .codex import CodexRuntime
from .offline import MockRuntime, NoAIRuntime


def create_runtime(config=None) -> AIRuntime:
    if config is None:
        from ..config import runtime_config
        config = runtime_config()
    kind = config.get("type", "codex_chatgpt_plan")
    if kind == "mock": return MockRuntime()
    if kind == "no_ai": return NoAIRuntime()
    if kind == "codex_chatgpt_plan":
        return CodexRuntime(config.get("executable", "codex"), timeout=config.get("rpc_timeout_seconds", 10),
                            turn_timeout=config.get("turn_timeout_seconds", 120))
    # Old API provider selection cannot accidentally activate metered billing.
    return NoAIRuntime()
