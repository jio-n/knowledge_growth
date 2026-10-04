"""Offline mock provider.

Purpose (ADR-003): the whole end-to-end flow (register → ask → save →
note → export) must be testable and demoable without any API key or
network. Answers are clearly labelled as mock output so they can never be
mistaken for real analysis.

- complete_json returns "{}" — callers fall back to heuristic extraction
  (app/analysis.py), which doubles as the offline behaviour.
- translate-hinted calls return the original text with a mock marker.
"""
from __future__ import annotations

from .base import LLMProvider, LLMResult

MOCK_NOTE = (
    "> ⚠️ **モック回答**: 開発・テスト用の回答です。"
    "実際のAI回答を使う場合は、Codexをインストールし、"
    "AI状態メニューの「ChatGPTで接続」から認証してください。\n\n"
)


class MockProvider(LLMProvider):
    name = "mock"

    def complete(self, system: str, user: str, *, max_tokens: int | None = None,
                 hint: str | None = None) -> LLMResult:
        if hint == "translate":
            text = "【モック翻訳】" + _selection_of(user)
        else:
            sel = _selection_of(user)
            text = MOCK_NOTE + (
                f"選択箇所:\n\n> {sel[:400]}\n\n"
                "モックプロバイダーは選択箇所と文脈を受け取り、この位置に回答を生成します。"
                "保存・アンカー・ノート機能はこの回答でもすべて動作します。"
            )
        return LLMResult(text=text, provider=self.name, model="mock")

    def complete_json(self, system: str, user: str, *, max_tokens: int | None = None) -> LLMResult:
        return LLMResult(text="{}", provider=self.name, model="mock")


def _selection_of(user_prompt: str) -> str:
    """Pull the ---SELECTION--- section out of the built prompt, if present."""
    marker = "---SELECTION---"
    if marker in user_prompt:
        seg = user_prompt.split(marker, 1)[1]
        end = seg.find("---")
        return (seg[:end] if end > 0 else seg).strip()
    return user_prompt[:200].strip()
