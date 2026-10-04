"""Codex-specific mapping, verified against CLI 0.159.0-alpha.3 schema.

Only account type chatgpt is accepted. Catalog entries are not entitlements.
"""
import json
import queue
import threading
import time
from urllib.parse import parse_qs, urlsplit
from .base import AIRuntime, AIError, AuthState, Capabilities, ModelInfo, RuntimeStatus, Session, TurnEvent
from .process import AppServerProcess, DISABLED_FEATURES
from ..prompts import get_prompt


class CodexRuntime(AIRuntime):
    name = "codex_chatgpt_plan"
    def __init__(self, executable="codex", *, timeout=10, turn_timeout=120):
        self.transport = AppServerProcess(executable, timeout=timeout,
                                          on_notification=self._notification, on_failure=self._failure)
        self.turn_timeout = turn_timeout
        self._tool_overrides = {}
        self._state = "disconnected"
        self._auth = AuthState()
        self._error = None
        self._login_id = None
        self._login_state = "idle"
        self._auth_dirty = False
        self._early_login_result = None
        self._auth_revision = 0
        self._lifecycle = threading.RLock()
        self._auth_lock = threading.RLock()

    def _failure(self, code):
        self._state, self._error = "error", code

    def _notification(self, method, params):
        if method == "account/login/completed":
            if self._login_state != "pending": return
            result = "completed" if params.get("success") is True else "failed"
            if self._login_id is None:
                self._early_login_result = (params.get("loginId"), result)
            elif params.get("loginId") == self._login_id:
                self._login_state = result
            else: return
            self._auth_revision += 1
            self._auth_dirty = True
        elif method == "account/updated":
            self._auth_revision += 1
            self._auth_dirty = True

    def start(self):
        with self._lifecycle:
            if self.transport.process: return
            self._state, self._error = "starting", None
            try:
                self.transport.start()
                self.transport.request("initialize", {"clientInfo": {"name": "knowledge_growth", "version": "0.4"},
                                                      "capabilities": None})
                self.transport.notify("initialized")
                self._disable_inherited_tools()
                self._read_account()
            except AIError as error:
                self.transport.close()
                self._state = "unavailable" if error.code in ("executable_missing", "native_executable_required") else "error"
                self._error = error.code

    def close(self):
        with self._lifecycle:
            self.transport.close()
            self._state, self._error = "disconnected", None
            self._login_id, self._login_state = None, "idle"
            self._auth = AuthState()

    def restart(self):
        with self._lifecycle:
            self.close()
            self.start()

    def _disable_inherited_tools(self):
        # Read only in memory; project cwd is the empty owned directory. Do not
        # retain raw config, layers, environment values, or tool credentials.
        result = self.transport.request("config/read", {"includeLayers": False, "cwd": self.transport.workdir.name})
        try:
            servers = result["config"].get("mcp_servers", {})
            if not isinstance(servers, dict): raise ValueError()
            self._tool_overrides = {f"mcp_servers.{json.dumps(name)}.enabled": False for name in servers}
        except (KeyError, TypeError, ValueError): raise AIError("malformed_config") from None

    def _read_account(self):
        revision = self._auth_revision
        result = self.transport.request("account/read", {"refreshToken": True})
        if "account" not in result: raise AIError("malformed_account")
        account = result["account"]
        if account is not None and not isinstance(account, dict): raise AIError("malformed_account")
        authenticated = bool(account and account.get("type") == "chatgpt")
        plan = account.get("planType") if authenticated else None
        # whitelist values: never expose arbitrary vendor strings / added fields.
        plan = plan if plan in ("free", "go", "plus", "pro", "team", "business", "enterprise", "edu", "unknown") else None
        self._auth = AuthState("authenticated" if authenticated else "signed_out", plan, self._login_state)
        self._state = "ready" if authenticated else "auth_required"
        self._error = None
        self._auth_dirty = revision != self._auth_revision

    def status(self, *, refresh=False):
        with self._auth_lock:
            if (refresh or self._auth_dirty) and self._state in ("ready", "auth_required"):
                try: self._read_account()
                except AIError as error:
                    self._failure(error.code)
            return RuntimeStatus(self.name, self._state,
                                 AuthState(self._auth.state, self._auth.plan, self._login_state), self._error)

    def capabilities(self):
        return Capabilities(inference=True, streaming=True, cancellation=True,
                            authentication=True, resume=True, model_catalog=True)

    def _require_ready(self):
        if self.status().state != "ready": raise AIError(self._state)

    def login(self):
        with self._auth_lock:
            if self._state not in ("ready", "auth_required"): raise AIError(self._state)
            if self._login_state == "pending": raise AIError("login_pending")
            self._login_state = "pending"
            self._login_id = None
            self._early_login_result = None
            try:
                result = self.transport.request("account/login/start", {"type": "chatgpt"})
                url = result.get("authUrl", "")
                parsed = urlsplit(url)
                sensitive_query = {"access_token", "id_token", "refresh_token", "authorization"}
                if (result.get("type") != "chatgpt" or not isinstance(result.get("loginId"), str)
                        or parsed.scheme != "https" or parsed.hostname != "auth.openai.com" or parsed.username or parsed.password
                        or parsed.port not in (None, 443) or parsed.fragment
                        or sensitive_query.intersection(parse_qs(parsed.query))):
                    raise AIError("malformed_login")
                self._login_id = result["loginId"]
                if self._early_login_result and self._early_login_result[0] == self._login_id:
                    self._login_state = self._early_login_result[1]
                self._early_login_result = None
                # OAuth URL is transient, returned only here; no account/raw payload.
                return {"authorization_url": url}
            except (AIError, ValueError, TypeError) as error:
                self._login_state = "failed"
                raise AIError(error.code if isinstance(error, AIError) else "malformed_login") from None

    def cancel_login(self):
        with self._auth_lock:
            if self._login_id:
                self.transport.request("account/login/cancel", {"loginId": self._login_id})
            self._login_id, self._login_state = None, "cancelled"
            self._read_account()

    def logout(self):
        with self._auth_lock:
            self.transport.request("account/logout")
            self._login_id, self._login_state = None, "idle"
            self._read_account()

    def models(self):
        if self._state not in ("ready", "auth_required"): raise AIError(self._state)
        models, cursor, seen = [], None, set()
        for _ in range(20):
            result = self.transport.request("model/list", {"cursor": cursor, "limit": 100, "includeHidden": False})
            try:
                for entry in result["data"]:
                    if not isinstance(entry["model"], str) or not isinstance(entry["displayName"], str): raise ValueError()
                    models.append(ModelInfo(entry["model"], entry["displayName"]))
                cursor = result["nextCursor"]
                if cursor is None: return models
                if not isinstance(cursor, str) or cursor in seen: raise ValueError()
                seen.add(cursor)
            except (KeyError, TypeError, ValueError): raise AIError("malformed_models") from None
        raise AIError("model_catalog_limit")

    def _session_params(self):
        return {"cwd": self.transport.workdir.name, "sandbox": "read-only", "approvalPolicy": "never",
                "modelProvider": "openai", "developerInstructions": get_prompt("ai_runtime").body,
                "config": {"web_search": "disabled", **self._tool_overrides,
                           **{f"features.{name}": False for name in DISABLED_FEATURES}}}

    def _session_result(self, result):
        try:
            session_id, model = result["thread"]["id"], result["model"]
            if not isinstance(session_id, str) or not isinstance(model, str): raise ValueError()
            return Session(session_id, model)
        except (KeyError, TypeError, ValueError): raise AIError("malformed_session") from None

    def create_session(self, *, instructions="", model=None):
        self._require_ready()
        params = {**self._session_params(), "baseInstructions": instructions}
        if model is not None: params["model"] = model
        return self._session_result(self.transport.request("thread/start", params))

    def resume_session(self, session_id):
        self._require_ready()
        return self._session_result(self.transport.request("thread/resume", {**self._session_params(), "threadId": session_id}))

    def send_turn(self, session, text, *, hint=None):
        self._require_ready()
        active = None
        finished = False
        with self.transport.subscribe(session.id) as inbox:
            try:
                result = self.transport.request("turn/start", {
                    "threadId": session.id, "input": [{"type": "text", "text": text, "text_elements": []}],
                    "approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly", "networkAccess": False}})
                try:
                    active = result["turn"]["id"]
                    if not isinstance(active, str): raise ValueError()
                except (KeyError, ValueError, TypeError): raise AIError("malformed_turn") from None
                yield TurnEvent("started", session.id, active)
                deadline = time.monotonic() + self.turn_timeout
                while True:
                    try: event = inbox.get(timeout=max(0, deadline - time.monotonic()))
                    except queue.Empty: raise AIError("turn_timeout") from None
                    if isinstance(event, AIError): raise event
                    method, params = event
                    try:
                        turn_id = params["turn"]["id"] if method == "turn/completed" else params["turnId"]
                        if turn_id != active: continue
                        if method == "item/agentMessage/delta":
                            if not isinstance(params["delta"], str): raise ValueError()
                            yield TurnEvent("delta", session.id, active, params["delta"])
                        else:
                            state = params["turn"]["status"]
                            if state not in ("completed", "interrupted", "failed"): raise ValueError()
                            finished = True
                            if state == "failed":
                                # Entitlement/limits/network outcomes are learned here,
                                # never inferred from the model catalog.
                                raise AIError("turn_failed")
                            yield TurnEvent("cancelled" if state == "interrupted" else "completed", session.id, active)
                            return
                    except (KeyError, TypeError, ValueError): raise AIError("malformed_turn_event") from None
            except AIError as error:
                self._failure(error.code)
                raise
            finally:
                if active and not finished:
                    try: self.cancel(session.id, active)
                    except AIError:
                        # Timeout/broken transport: kill the owned process to stop work.
                        self.transport.close()

    def cancel(self, session_id, turn_id):
        self.transport.request("turn/interrupt", {"threadId": session_id, "turnId": turn_id})
