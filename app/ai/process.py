"""Bounded newline JSON RPC transport. stdout is protocol; stderr is discarded.

Never log raw messages, exceptions from the child, prompts, or auth URLs.
Only fixed error codes cross this boundary. EOF is the stdio shutdown signal.
"""
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
from concurrent.futures import Future, TimeoutError
from contextlib import contextmanager
from .base import AIError

MAX_FRAME = 2 * 1024 * 1024
DISABLED_FEATURES = ("shell_tool", "shell_snapshot", "apps", "plugins", "remote_plugin",
                     "multi_agent", "browser_use", "computer_use", "in_app_browser")


def discover_executable(executable, *, platform=None):
    path = shutil.which(executable)
    if not path: raise AIError("executable_missing")
    if (platform or os.name) == "nt" and path.lower().endswith((".cmd", ".bat")):
        # npm's shell shim is never executed. Find its bundled native binary.
        package = Path(path).parent / "node_modules" / "@openai" / "codex"
        patterns = ("vendor/*/codex/codex.exe", "node_modules/@openai/codex-win32-*/vendor/*/codex/codex.exe")
        for pattern in patterns:
            candidates = sorted(package.glob(pattern))
            if candidates: return str(candidates[0])
        raise AIError("native_executable_required")
    return path


def child_environment():
    # Keep OS/keyring/proxy essentials; never inherit metered provider credentials.
    allowed = {"PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "WINDIR",
               "TEMP", "TMP", "TMPDIR", "CODEX_HOME", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
               "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"}
    return {**{k: v for k, v in os.environ.items() if k in allowed}, "RUST_LOG": "off"}


class AppServerProcess:
    def __init__(self, executable="codex", *, timeout=10, on_notification=None, on_failure=None):
        self.executable = executable
        self.timeout = timeout
        self.on_notification = on_notification or (lambda method, params: None)
        self.on_failure = on_failure or (lambda code: None)
        self.process = None
        self.workdir = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._pending = {}
        self._writes = set()
        self._writer_queue = queue.Queue(maxsize=128)
        self._writer = None
        self._subscriptions = {}
        self._next_id = 0
        self._closing = False
        self._reader = None
        self._stderr = None
        self._job = None
        self._failure_code = None

    def start(self):
        path = discover_executable(self.executable)
        self._closing = False
        self._failure_code = None
        self.workdir = tempfile.TemporaryDirectory(prefix="kg-ai-")
        args = [path, "app-server", "--listen", "stdio://", "-c", 'cli_auth_credentials_store="keyring"',
                "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
                "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"',
                "-c", 'shell_environment_policy.inherit="none"', "-c", 'web_search="disabled"']
        for feature in DISABLED_FEATURES:
            args.extend(["-c", f"features.{feature}=false"])
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            cwd=self.workdir.name, env=child_environment(), shell=False,
                                            start_new_session=(os.name != "nt"))
            if os.name == "nt":
                from .windows_job import WindowsJob
                self._job = WindowsJob(self.process)
        except OSError:
            self.close()
            raise AIError("process_start_failed") from None
        self._writer_queue = queue.Queue(maxsize=128)
        self._writer = threading.Thread(target=self._write_frames, name="kg-codex-writer", daemon=True)
        self._reader = threading.Thread(target=self._read, name="kg-codex-rpc", daemon=True)
        self._stderr = threading.Thread(target=self._drain_stderr, name="kg-codex-stderr", daemon=True)
        self._writer.start()
        self._reader.start()
        self._stderr.start()

    def _drain_stderr(self):
        # Deliberately drop diagnostics; redaction by suppression includes arbitrary
        # multiline tokens and Authorization values that regexes could miss.
        while self.process.stderr.read(4096): pass

    def _write_frames(self):
        while True:
            entry = self._writer_queue.get()
            if entry is None: return
            frame, future = entry
            try:
                with self._write_lock:
                    if self._closing or self._failure_code: raise AIError("process_disconnected")
                    self.process.stdin.write(frame)
                    self.process.stdin.flush()
                with self._lock:
                    if not future.done(): future.set_result(None)
            except (OSError, ValueError, AIError):
                with self._lock:
                    if not future.done(): future.set_exception(AIError("process_disconnected"))

    def _write(self, payload):
        frame = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
        if len(frame) > MAX_FRAME: raise AIError("request_too_large")
        future = Future()
        with self._lock:
            if self._closing or self._failure_code or not self.process or self.process.poll() is not None:
                raise AIError("process_disconnected")
            self._writes.add(future)
        try:
            try: self._writer_queue.put_nowait((frame, future))
            except queue.Full: raise AIError("write_queue_full") from None
            future.result(timeout=self.timeout)
        except TimeoutError:
            self._fail("rpc_timeout")
            raise AIError("rpc_timeout") from None
        finally:
            with self._lock: self._writes.discard(future)

    def notify(self, method): self._write({"method": method})

    def request(self, method, params=None):
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            future = Future()
            self._pending[request_id] = future
        try:
            payload = {"id": request_id, "method": method}
            if params is not None: payload["params"] = params
            self._write(payload)
            return future.result(timeout=self.timeout)
        except TimeoutError:
            self._fail("rpc_timeout")
            raise AIError("rpc_timeout") from None
        finally:
            with self._lock: self._pending.pop(request_id, None)

    def _fail(self, code):
        with self._lock:
            if self._failure_code: return
            self._failure_code = code
            for future in list(self._pending.values()) + list(self._writes):
                if not future.done(): future.set_exception(AIError(code))
            for inbox in self._subscriptions.values():
                try: inbox.put_nowait(AIError(code))
                except queue.Full: pass
        if not self._closing:
            self.on_failure(code)
            if code in ("malformed_protocol", "rpc_timeout") and self.process:
                # A broken framing/request channel cannot safely cancel work.
                # Stop the owned process immediately; close/restart reaps it.
                if self._job: self._job.close()
                elif os.name != "nt":
                    try: os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                else:
                    try: self.process.kill()
                    except OSError: pass

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_FRAME + 1)
                if not line: break
                if len(line) > MAX_FRAME or not line.endswith(b"\n"): raise ValueError()
                message = json.loads(line)
                if not isinstance(message, dict): raise ValueError()
                if "method" in message:
                    method, params = message["method"], message.get("params", {})
                    if not isinstance(method, str) or not isinstance(params, dict): raise ValueError()
                    if "id" in message:
                        # No execution, file-write approval, external token refresh,
                        # or tool callback is accepted by this client.
                        self._write({"id": message["id"], "error": {"code": -32601, "message": "Unsupported request"}})
                        continue
                    self.on_notification(method, params)
                    with self._lock:
                        inbox = self._subscriptions.get(params.get("threadId"))
                        if inbox and method in ("item/agentMessage/delta", "turn/completed"):
                            inbox.put_nowait((method, params))
                elif "id" in message:
                    if not isinstance(message["id"], (int, str)) or ("result" in message) == ("error" in message):
                        raise ValueError()
                    with self._lock:
                        future = self._pending.get(message["id"])
                        if future and not future.done():
                            if "error" in message: future.set_exception(AIError("rpc_rejected"))
                            elif not isinstance(message["result"], dict): raise ValueError()
                            else: future.set_result(message["result"])
                else: raise ValueError()
        except (ValueError, UnicodeError, OSError, AIError, queue.Full, TypeError):
            self._fail("malformed_protocol")
            return
        self._fail("process_exited")

    @contextmanager
    def subscribe(self, session_id):
        inbox = queue.Queue(maxsize=4096)
        with self._lock:
            if session_id in self._subscriptions: raise AIError("session_busy")
            self._subscriptions[session_id] = inbox
        try: yield inbox
        finally:
            with self._lock: self._subscriptions.pop(session_id, None)

    def close(self):
        self._closing = True
        self._fail("process_disconnected")
        process = self.process
        if process:
            if process.stdin and self._write_lock.acquire(timeout=.2):
                try:
                    try: process.stdin.close()
                    except OSError: pass
                finally: self._write_lock.release()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                else: process.terminate()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        try: os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                    else: process.kill()
                    process.wait(timeout=2)
            # A normally exiting parent can still leave descendants. Stop the
            # entire owned group/job before joining readers of inherited pipes.
            if self._job:
                self._job.close()
                self._job = None
            elif os.name != "nt":
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            if self._writer: self._writer_queue.put(None)
            for thread in (self._reader, self._stderr, self._writer):
                if thread and thread is not threading.current_thread(): thread.join(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream: stream.close()
        self.process = None
        if self.workdir: self.workdir.cleanup(); self.workdir = None
