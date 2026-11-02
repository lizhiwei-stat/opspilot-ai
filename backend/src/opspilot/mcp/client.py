"""Backend MCP client hub: launches the allowlisted OpsPilot MCP servers as stdio
subprocesses (official SDK `Client` + `StdioServerParameters`), discovers their tools with
`tools/list`, and calls them with `tools/call` on behalf of the LangGraph agent.

Untrusted-server controls (a server is a separate process and is treated as untrusted):
  * server allowlist   - only servers in the registry can be launched; fixed module command,
                         no shell, no user/LLM-controlled command, path or URL
  * identity check     - discovered serverInfo.name must match the registry
  * tool allowlist     - only contract tools are exposed; extra advertised tools are ignored
                         (and reported); a missing tool marks it unavailable
  * schema check       - advertised input schema properties must equal the contract fields
  * output checks      - size limit on content + structured output; structured_content must
                         validate against the contract output model; errors map to classes
  * timeouts           - connect timeout, per-call timeout (the SDK cancels the request)
  * crash recovery     - a dead server process is relaunched on the next call
  * least privilege    - the subprocess env is built from scratch: no JWT secret, no LLM API
                         key, no paid-API flags; only non-secret settings + a derived caller key

The hub runs its own asyncio loop in a daemon thread so the synchronous LangGraph/ToolGuard
code can call it with a blocking `call()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from mcp import Client, StdioServerParameters
from mcp.types import TextContent
from pydantic import BaseModel, SecretStr, ValidationError

from opspilot.agent.tools import (
    ToolError,
    ToolNotFound,
    ToolPermissionError,
    ToolTransientError,
    ToolUpstreamError,
)
from opspilot.core.config import Settings
from opspilot.core.redact import redact
from opspilot.mcp.contracts import SERVER_TOOLS
from opspilot.mcp.identity import META_KEY, derive_caller_secret, mint

log = structlog.get_logger("opspilot.mcp.client")
SRC = Path(__file__).resolve().parents[2]  # backend/src
ROOT = SRC.parents[1]


class ToolConflictError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


class ServerUnavailable(ToolTransientError):
    pass


_CLASS = {
    "permission": ToolPermissionError,
    "not_found": ToolNotFound,
    "conflict": ToolConflictError,
    "transient": ToolTransientError,
    "upstream": ToolUpstreamError,
    "internal": ToolUpstreamError,
}


@dataclass(frozen=True)
class ServerLaunch:
    name: str
    module: str  # python -m <module>; never a shell string
    expected_name: str
    tools: dict[str, tuple[type[BaseModel], type[BaseModel]]]


REGISTRY: dict[str, ServerLaunch] = {
    n: ServerLaunch(n, f"opspilot.mcp.servers.{n}", f"opspilot-{n}", SERVER_TOOLS[n])
    for n in ("knowledge", "ticket", "diagnostics")
}


@dataclass
class CallerCtx:
    user_id: str
    role: str
    thread_id: str | None
    run_id: str | None
    call_id: str | None


@dataclass
class ToolInfo:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    read_only: bool | None


@dataclass
class _Conn:
    launch: ServerLaunch
    queue: asyncio.Queue | None = None
    ready: Future | None = None
    alive: bool = False
    tools: dict[str, ToolInfo] = field(default_factory=dict)
    ignored: list[str] = field(default_factory=list)
    protocol: str | None = None
    pid_hint: int = 0
    launches: int = 0


_SECRET_FIELD = re.compile(r"(secret|password|api_key|token|paid_|allow_paid)", re.I)


def child_env(s: Settings, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Minimal env for an MCP server subprocess (least privilege)."""
    env: dict[str, str] = {}
    for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "HF_HOME", "HF_HUB_OFFLINE", "SSL_CERT_FILE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONUNBUFFERED"] = "1"
    for name, value in s.model_dump().items():
        fld = type(s).model_fields.get(name)
        if fld is None or _SECRET_FIELD.search(name) or isinstance(getattr(s, name), SecretStr):
            continue
        if value is None:
            continue
        env[f"OPSPILOT_{name.upper()}"] = (
            json.dumps(list(value) if isinstance(value, tuple) else value)
            if isinstance(value, (list, tuple, dict))
            else str(value)
        )
    env["OPSPILOT_MCP_CALLER_SECRET"] = derive_caller_secret(s.jwt_secret.get_secret_value())
    env["OPSPILOT_JWT_SECRET"] = secrets.token_hex(32)  # unused placeholder (validator)
    env["OPSPILOT_LLM_PROVIDER"] = "mock"
    if s.embedding_provider == "openai_compatible":  # paid embeddings never run in MCP servers
        env["OPSPILOT_EMBEDDING_PROVIDER"] = "mock"
    env.update(extra or {})
    return env


class MCPHub:
    def __init__(
        self,
        settings: Settings,
        registry: dict[str, ServerLaunch] | None = None,
        *,
        extra_env: dict[str, str] | None = None,
        session_factory: Any = None,
        transport_override: dict[str, Any] | None = None,
    ) -> None:
        self.s = settings
        self.registry = dict(registry or REGISTRY)
        self.extra_env = extra_env or {}
        self.sf = session_factory
        self.transport_override = transport_override or {}  # name -> transport (tests/HTTP)
        self._secret = derive_caller_secret(settings.jwt_secret.get_secret_value())
        self._conns = {n: _Conn(launch) for n, launch in self.registry.items()}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-hub", daemon=True)
        self._thread.start()
        self._lock = threading.Lock()
        self.closed = False
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ lifecycle
    def _params(self, launch: ServerLaunch) -> Any:
        if launch.name in self.transport_override:
            return self.transport_override[launch.name]
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", launch.module],
            env=child_env(self.s, self.extra_env),
            cwd=str(ROOT),
        )

    async def _worker(self, conn: _Conn) -> None:
        launch = conn.launch
        if conn.queue is None or conn.ready is None:
            raise RuntimeError("connection not initialised")
        try:
            async with Client(self._params(launch)) as c:
                info = c.server_info
                if info is None or info.name != launch.expected_name:
                    raise ToolUpstreamError(
                        f"server identity mismatch: expected {launch.expected_name}, "
                        f"got {getattr(info, 'name', None)}"
                    )
                listed = await c.list_tools()
                tools, ignored = {}, []
                for t in listed.tools:
                    contract = launch.tools.get(t.name)
                    if contract is None:
                        ignored.append(t.name)
                        continue
                    props = set((t.input_schema or {}).get("properties", {}))
                    if props != set(contract[0].model_fields):
                        ignored.append(f"{t.name}:schema_mismatch")
                        continue
                    ann = t.annotations
                    tools[t.name] = ToolInfo(
                        launch.name,
                        t.name,
                        t.description or "",
                        t.input_schema,
                        t.output_schema,
                        getattr(ann, "read_only_hint", None) if ann else None,
                    )
                if ignored:
                    log.warning("mcp_tools_ignored", server=launch.name, tools=ignored)
                conn.tools, conn.ignored, conn.protocol = tools, ignored, c.protocol_version
                conn.alive = True
                conn.ready.set_result(True)
                while True:
                    job = await conn.queue.get()
                    if job is None:
                        break
                    coro_fn, fut = job
                    task = asyncio.create_task(self._run_job(c, coro_fn, fut))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
        except BaseException as exc:
            if conn.ready and not conn.ready.done():
                conn.ready.set_exception(
                    ServerUnavailable(
                        f"{launch.name}: {type(exc).__name__}: {redact(str(exc))[:200]}"
                    )
                )
            log.warning("mcp_server_down", server=launch.name, error=type(exc).__name__)
        finally:
            conn.alive = False
            q = conn.queue
            while q is not None and not q.empty():
                j = q.get_nowait()
                if j is not None and not j[1].done():
                    j[1].set_exception(ServerUnavailable(f"{launch.name} unavailable"))

    @staticmethod
    async def _run_job(c: Client, coro_fn: Any, fut: Future) -> None:
        try:
            res = await coro_fn(c)
            if not fut.done():
                fut.set_result(res)
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)

    def _ensure(self, name: str) -> _Conn:
        if self.closed:
            raise ServerUnavailable("hub closed")
        conn = self._conns.get(name)
        if conn is None:
            raise ToolPermissionError(f"MCP server {name!r} is not allowlisted")
        with self._lock:
            if not conn.alive and (conn.ready is None or conn.ready.done()):
                conn.ready = Future()
                conn.launches += 1

                def boot() -> None:
                    conn.queue = asyncio.Queue()
                    self._loop.create_task(self._worker(conn))

                self._loop.call_soon_threadsafe(boot)
            ready = conn.ready
        try:
            ready.result(timeout=self.s.mcp_connect_timeout_s)
        except FutTimeout as exc:
            raise ServerUnavailable(f"{name}: connect timeout") from exc
        return conn

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for c in self._conns.values():
            if c.queue is not None and c.alive:
                self._loop.call_soon_threadsafe(c.queue.put_nowait, None)
        time.sleep(0.2)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------ API
    def warm(self) -> None:
        """Launch all allowlisted servers in parallel (connect + tools/list)."""
        ths = [threading.Thread(target=self._safe_ensure, args=(n,)) for n in self.registry]
        for t in ths:
            t.start()
        for t in ths:
            t.join()

    def _safe_ensure(self, name: str) -> None:
        with contextlib.suppress(ToolError):
            self._ensure(name)

    def discover(self) -> dict[str, dict[str, Any]]:
        self.warm()
        out = {}
        for n in self.registry:
            try:
                c = self._ensure(n)
                out[n] = {
                    "alive": c.alive,
                    "protocol": c.protocol,
                    "tools": {
                        k: {
                            "description": v.description,
                            "input_schema": v.input_schema,
                            "read_only": v.read_only,
                        }
                        for k, v in c.tools.items()
                    },
                    "ignored": list(c.ignored),
                    "launches": c.launches,
                }
            except ToolError as exc:
                out[n] = {"alive": False, "error": str(exc)}
        return out

    def call(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        caller: CallerCtx,
        timeout_s: float | None = None,
    ) -> BaseModel:
        launch = self.registry.get(server)
        if launch is None or tool not in launch.tools:
            raise ToolPermissionError(f"tool {server}.{tool} is not allowlisted")
        in_model, out_model = launch.tools[tool]
        try:
            clean = in_model.model_validate(args).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            raise ToolValidationError(f"invalid arguments: {exc.error_count()} error(s)") from exc
        t0 = time.perf_counter()
        timeout = timeout_s or self.s.mcp_call_timeout_s
        try:
            conn = self._ensure(server)
        except ToolError as exc:
            self._client_audit(server, tool, caller, "unavailable", t0, str(exc), clean)
            raise
        if tool not in conn.tools:
            self._client_audit(server, tool, caller, "unavailable", t0, "tool not offered", clean)
            raise ServerUnavailable(f"{server}.{tool} not offered by the server")
        token = mint(
            self._secret,
            server=server,
            tool=tool,
            args=clean,
            user_id=caller.user_id,
            role=caller.role,
            thread_id=caller.thread_id,
            run_id=caller.run_id,
            call_id=caller.call_id,
            ttl_s=self.s.mcp_caller_token_ttl_s,
        )

        async def job(c: Client) -> Any:
            return await c.call_tool(
                tool, clean, read_timeout_seconds=timeout, meta={META_KEY: token}
            )

        fut: Future = Future()
        q = conn.queue
        if q is None:
            raise ServerUnavailable(f"{server} not connected")
        self._loop.call_soon_threadsafe(q.put_nowait, (job, fut))
        try:
            res = fut.result(timeout=timeout + 1.0)
        except FutTimeout as exc:
            self._client_audit(
                server, tool, caller, "timeout", t0, f"no response in {timeout}s", clean
            )
            raise TimeoutError(f"MCP {server}.{tool} timed out after {timeout}s") from exc
        except ServerUnavailable as exc:
            self._client_audit(server, tool, caller, "unavailable", t0, str(exc), clean)
            raise
        except Exception as exc:  # SDK/transport error (MCPError, timeout from read_timeout)
            name = type(exc).__name__
            if "timeout" in name.lower() or "timed out" in str(exc).lower():
                self._client_audit(server, tool, caller, "timeout", t0, name, clean)
                raise TimeoutError(f"MCP {server}.{tool} timed out after {timeout}s") from exc
            self._client_audit(server, tool, caller, "error", t0, name, clean)
            raise ToolUpstreamError(f"MCP protocol error: {name}") from exc
        return self._parse(server, tool, res, out_model, caller, t0, clean)

    def _parse(self, server, tool, res, out_model, caller, t0, clean) -> BaseModel:  # type: ignore[no-untyped-def]
        text = "".join(b.text for b in res.content if isinstance(b, TextContent))
        size = len(text.encode()) + len(
            json.dumps(res.structured_content or {}, ensure_ascii=False).encode()
        )
        if size > 2 * self.s.mcp_max_output_bytes:
            self._client_audit(server, tool, caller, "error", t0, "output_too_large", clean)
            raise ToolUpstreamError("MCP output exceeds size limit")
        if res.is_error:
            m = re.search(
                r"\[(permission|not_found|conflict|transient|upstream|internal)\]\s*(.*)",
                text,
                re.S,
            )
            if m:
                raise _CLASS[m.group(1)](m.group(2).strip()[:300])
            if "rejected arguments" in text or "validation error" in text:
                raise ToolValidationError(text[:300])
            raise ToolUpstreamError(text[:300] or "tool failed")
        try:
            return out_model.model_validate(res.structured_content)
        except ValidationError as exc:
            self._client_audit(server, tool, caller, "invalid", t0, "output_schema_mismatch", clean)
            raise ToolUpstreamError("MCP output does not match the contract") from exc

    def _client_audit(self, server, tool, caller, status, t0, err, args) -> None:  # type: ignore[no-untyped-def]
        if self.sf is None:
            return
        from opspilot.agent.tools import _summary
        from opspilot.db.models import McpToolAudit
        from opspilot.mcp.identity import args_sha256

        try:
            with self.sf() as s:
                s.add(
                    McpToolAudit(
                        side="client",
                        server=server,
                        tool=tool,
                        transport="stdio" if server not in self.transport_override else "custom",
                        user_id=uuid.UUID(caller.user_id) if caller.user_id else None,
                        user_role=caller.role,
                        thread_id=caller.thread_id,
                        run_id=caller.run_id,
                        call_id=caller.call_id,
                        args_summary=json.loads(
                            redact(json.dumps(_summary(args, 120), ensure_ascii=False))
                        ),
                        args_sha256=args_sha256(args),
                        status=status,
                        error=redact(err)[:500],
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                    )
                )
                s.commit()
        except Exception:
            log.warning("mcp_client_audit_failed", server=server, tool=tool)


_HUBS: dict[str, MCPHub] = {}
_HUBS_LOCK = threading.Lock()


def get_hub(settings: Settings, session_factory: Any = None) -> MCPHub:
    """One hub (one set of server processes) per database + secret."""
    fp = derive_caller_secret(settings.jwt_secret.get_secret_value())[:12]
    key = f"{settings.database_url}|{fp}"
    with _HUBS_LOCK:
        hub = _HUBS.get(key)
        if hub is None or hub.closed:
            hub = _HUBS[key] = MCPHub(settings, session_factory=session_factory)
        elif hub.sf is None and session_factory is not None:
            hub.sf = session_factory
        return hub


def close_all() -> None:
    with _HUBS_LOCK:
        for h in _HUBS.values():
            h.close()
        _HUBS.clear()
