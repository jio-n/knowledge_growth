"""OpenAI-compatible chat completions provider.

Works with OpenAI, Azure OpenAI (gateway), Ollama (http://localhost:11434/v1),
LM Studio, vLLM, etc. — anything speaking /chat/completions.
"""
from __future__ import annotations

import os

import httpx

from .base import LLMError, LLMProvider, LLMResult


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self, cfg: dict, timeout: int = 120):
        self.model = cfg.get("model", "gpt-4o-mini")
        self.base_url = cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.max_tokens = cfg.get("max_tokens", 2048)
        self.timeout = timeout
        key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = os.environ.get(key_env, "")
        # local endpoints (Ollama 等) はキー不要のため空でも続行する

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 hint: str | None = None) -> LLMResult:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "max_tokens": max_tokens or self.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            headers=headers,
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise LLMError(f"LLM API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        return LLMResult(text=text, provider=self.name, model=data.get("model", self.model))
