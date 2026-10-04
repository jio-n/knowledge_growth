"""Vendor-neutral synchronous runtime contract; no research objects or RPC here."""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Iterator


class AIError(RuntimeError):
    """Safe public error: vendor payloads must never be attached."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"AI未接続 / {code}")


@dataclass(frozen=True)
class AuthState:
    state: str = "signed_out"
    plan: str | None = None
    login_state: str = "idle"


@dataclass(frozen=True)
class RuntimeStatus:
    runtime: str
    state: str
    auth: AuthState = field(default_factory=AuthState)
    error: str | None = None

    def public(self):
        return asdict(self)


@dataclass(frozen=True)
class Capabilities:
    inference: bool = False
    streaming: bool = False
    cancellation: bool = False
    authentication: bool = False
    resume: bool = False
    model_catalog: bool = False


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    entitlement: str = "unverified"


@dataclass(frozen=True)
class Session:
    id: str
    model: str | None = None


@dataclass(frozen=True)
class TurnEvent:
    kind: str                 # started / delta / completed / cancelled
    session_id: str
    turn_id: str
    text: str = ""


class AIRuntime(ABC):
    @abstractmethod
    def start(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    def restart(self) -> None:
        self.close()
        self.start()
    @abstractmethod
    def status(self, *, refresh: bool = False) -> RuntimeStatus: ...
    @abstractmethod
    def capabilities(self) -> Capabilities: ...
    @abstractmethod
    def models(self) -> list[ModelInfo]: ...
    @abstractmethod
    def create_session(self, *, instructions: str = "", model: str | None = None) -> Session: ...
    @abstractmethod
    def resume_session(self, session_id: str) -> Session: ...
    @abstractmethod
    def send_turn(self, session: Session, text: str, *, hint: str | None = None) -> Iterator[TurnEvent]: ...
    @abstractmethod
    def cancel(self, session_id: str, turn_id: str) -> None: ...
    def login(self) -> dict:
        raise AIError("authentication_unsupported")
    def cancel_login(self) -> None:
        raise AIError("authentication_unsupported")
    def logout(self) -> None:
        raise AIError("authentication_unsupported")
