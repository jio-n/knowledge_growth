"""Provider-agnostic LLM interface.

Contract: complete(system, user) -> LLMResult. No provider SDKs are used;
each provider is a thin HTTP client. Adding a provider = one file
implementing LLMProvider + a config entry (docs/architecture/ai_pipeline.md).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str


class LLMError(RuntimeError):
    pass


class LLMProvider:
    name = "base"

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 hint: str | None = None) -> LLMResult:
        """`hint` is an optional intent tag ("translate", "answer", ...).
        Real providers ignore it; the mock provider uses it to shape output."""
        raise NotImplementedError

    def complete_json(self, system: str, user: str, *, max_tokens: int | None = None) -> LLMResult:
        """Same as complete but the prompt demands strict JSON output.
        Providers may add JSON-mode parameters when available."""
        return self.complete(system + "\n\n出力は有効なJSONのみ。説明文やコードフェンスを含めないこと。",
                             user, max_tokens=max_tokens)


def strip_json_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    return t
