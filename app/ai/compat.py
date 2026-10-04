"""Connection point for legacy extraction, translate, question and future Brief.

Trusted application instructions stay separate from untrusted paper/user text.
This does not introduce ConversationThread or persist Codex thread IDs.
"""
from .base import AIRuntime, AIError
from ..llm.base import LLMError, LLMProvider, LLMResult


class RuntimeProvider(LLMProvider):
    def __init__(self, runtime: AIRuntime):
        self.runtime = runtime
        self.name = runtime.status().runtime

    def complete(self, system, user, *, max_tokens=None, hint=None):
        try:
            session = self.runtime.create_session(instructions=system)
            parts = []
            length, completed = 0, False
            for event in self.runtime.send_turn(session, user, hint=hint):
                if event.kind == "delta":
                    length += len(event.text)
                    if length > 1024 * 1024:
                        self.runtime.cancel(event.session_id, event.turn_id)
                        raise AIError("output_too_large")
                    parts.append(event.text)
                elif event.kind == "cancelled": raise AIError("cancelled")
                elif event.kind == "completed": completed = True
            if not completed: raise AIError("incomplete_turn")
            return LLMResult("".join(parts), self.name, session.model or "runtime-default")
        except AIError as error:
            raise LLMError(str(error)) from None

    def complete_json(self, system, user, *, max_tokens=None):
        return self.complete(system + "\n\n出力は有効なJSONのみ。説明文やコードフェンスを含めないこと。",
                             user, max_tokens=max_tokens, hint="json")
