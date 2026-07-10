"""Anthropic Messages API via plain HTTP (no SDK)."""
from __future__ import annotations

import os

import httpx

from .base import LLMError, LLMProvider, LLMResult


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, cfg: dict, timeout: int = 120):
        self.model = cfg.get("model", "claude-sonnet-5")
        self.base_url = cfg.get("base_url", "https://api.anthropic.com").rstrip("/")
        self.max_tokens = cfg.get("max_tokens", 2048)
        self.timeout = timeout
        key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        self.api_key = os.environ.get(key_env, "")
        if not self.api_key:
            raise LLMError(
                f"環境変数 {key_env} が設定されていません。"
                "config/app.config.json の llm.provider を 'mock' にするか、APIキーを設定してください。")

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 hint: str | None = None) -> LLMResult:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        r = httpx.post(
            f"{self.base_url}/v1/messages",
            json=payload,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise LLMError(f"Anthropic API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return LLMResult(text=text, provider=self.name, model=data.get("model", self.model))
