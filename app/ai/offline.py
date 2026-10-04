"""Explicit offline modes; mock preserves the existing intent-shaped fixtures."""
from uuid import uuid4
from .base import AIRuntime, AIError, AuthState, Capabilities, ModelInfo, RuntimeStatus, Session, TurnEvent


class NoAIRuntime(AIRuntime):
    name = "no_ai"
    def start(self): pass
    def close(self): pass
    def status(self, *, refresh=False):
        return RuntimeStatus(self.name, "disconnected", AuthState("not_applicable"))
    def capabilities(self): return Capabilities()
    def models(self): return []
    def create_session(self, **kwargs): raise AIError("disconnected")
    def resume_session(self, session_id): raise AIError("disconnected")
    def send_turn(self, session, text, *, hint=None): raise AIError("disconnected")
    def cancel(self, session_id, turn_id): raise AIError("disconnected")


class MockRuntime(NoAIRuntime):
    name = "mock"
    def __init__(self):
        self.sessions = {}
        self.cancelled = set()
    def close(self):
        self.sessions.clear()
        self.cancelled.clear()
    def status(self, *, refresh=False):
        return RuntimeStatus(self.name, "ready", AuthState("not_applicable"))
    def capabilities(self):
        return Capabilities(inference=True, streaming=True, cancellation=True, resume=True, model_catalog=True)
    def models(self): return [ModelInfo("mock", "Offline mock", "mock")]
    def create_session(self, *, instructions="", model=None):
        session = Session(str(uuid4()), "mock")
        self.sessions[session.id] = instructions
        return session
    def resume_session(self, session_id):
        if session_id not in self.sessions: raise AIError("session_missing")
        return Session(session_id, "mock")
    def send_turn(self, session, text, *, hint=None):
        from ..llm.mock import MockProvider
        turn = str(uuid4())
        yield TurnEvent("started", session.id, turn)
        if turn in self.cancelled:
            self.cancelled.discard(turn)
            yield TurnEvent("cancelled", session.id, turn)
            return
        result = (MockProvider().complete_json(self.sessions[session.id], text) if hint == "json"
                  else MockProvider().complete(self.sessions[session.id], text, hint=hint))
        reply = "runtime-ok" if text == "Reply with exactly: runtime-ok" else result.text
        yield TurnEvent("delta", session.id, turn, reply)
        yield TurnEvent("completed", session.id, turn)
    def cancel(self, session_id, turn_id): self.cancelled.add(turn_id)
