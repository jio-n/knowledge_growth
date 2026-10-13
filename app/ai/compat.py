"""Connection point for legacy extraction, translate, question and future Brief.

Trusted application instructions stay separate from untrusted paper/user text.
This does not introduce ConversationThread or persist Codex thread IDs.
"""
from .base import AIRuntime, AIError
from ..llm.base import LLMError, LLMProvider, LLMResult


class RuntimeLLMError(LLMError):
    """Typed adapter failure; consumers must classify code, never parse payloads."""
    def __init__(self, code):
        self.code = code
        # Fixed public message, independent of any vendor/error payload.
        safe = code if code in {"rpc_timeout", "turn_timeout", "incomplete_turn", "cancelled", "output_too_large"} else "runtime_error"
        super().__init__(f"AI未接続 / {safe}")


class RuntimeProvider(LLMProvider):
    def __init__(self, runtime: AIRuntime):
        self.runtime = runtime
        # Runtime identity must not perform an unbudgeted account/RPC refresh.
        self.name = getattr(runtime, "name", None) or runtime.status().runtime

    def complete(self, system, user, *, max_tokens=None, hint=None, deadline=None):
        try:
            session = (self.runtime.create_session(instructions=system) if deadline is None
                       else self.runtime.create_session_until(instructions=system, deadline=deadline))
            parts = []
            length, completed = 0, False
            events = (self.runtime.send_turn(session, user, hint=hint) if deadline is None
                      else self.runtime.send_turn_until(session, user, hint=hint, deadline=deadline))
            for event in events:
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
            raise RuntimeLLMError(error.code) from None

    def complete_json(self, system, user, *, max_tokens=None, deadline=None):
        return self.complete(system + "\n\n出力は有効なJSONのみ。説明文やコードフェンスを含めないこと。",
                             user, max_tokens=max_tokens, hint="json", deadline=deadline)
