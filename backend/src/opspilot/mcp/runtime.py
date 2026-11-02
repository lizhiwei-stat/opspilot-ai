"""Shared runtime of the three OpsPilot MCP servers.

Every tool body goes through ``Runtime.run_tool``:
  1. caller identity  - verify the caller token from request `_meta` (signature, audience =
                        this server, tool binding, argument hash, expiry, single use)
  2. user + ACL       - re-load the acting user from the DB (role is never trusted from the
                        token or the arguments); tool implementations apply per-tool ACL
  3. execution        - optional fault injection (only when OPSPILOT_AGENT_FAULT_INJECTION)
  4. output limit     - serialized output must fit OPSPILOT_MCP_MAX_OUTPUT_BYTES
  5. audit            - one mcp_tool_audit row (side='server'), args summarized + redacted
  6. errors           - raised as ToolError with a stable "[class] message" prefix (the
                        client maps it back); internal errors never leak details

The process reads settings from its ENVIRONMENT ONLY (no .env file): the backend launches it
with a minimal env (DB URL, caller secret, embedding config), never the JWT secret or any LLM
API key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from opspilot.agent.tools import (
    ToolContext,
    ToolNotFound,
    ToolPermissionError,
    ToolTransientError,
    _load_user,
    _summary,
)
from opspilot.core.config import Settings
from opspilot.core.redact import redact
from opspilot.db.models import McpToolAudit
from opspilot.mcp.identity import META_KEY, CallerError, ReplayCache, args_sha256, verify

log = structlog.get_logger("opspilot.mcp")

ImplFn = Callable[[Session, Any, BaseModel, ToolContext], BaseModel]


def _configure_logging() -> None:
    # stdio servers must never write to stdout (it carries the JSON-RPC stream)
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )


class Runtime:
    def __init__(self, server: str, transport: str = "stdio") -> None:
        from opspilot.mcp.impl import ToolConflict

        self._conflict = ToolConflict
        self.server = server
        self.transport = transport
        self.settings = Settings(_env_file=None)  # type: ignore[call-arg]
        secret = os.environ.get("OPSPILOT_MCP_CALLER_SECRET", "")
        if len(secret) < 32:
            raise SystemExit("OPSPILOT_MCP_CALLER_SECRET missing: refusing to start")
        self._secret = secret
        self.engine = create_engine(self.settings.database_url, pool_size=2, max_overflow=2)
        self.sf = sessionmaker(self.engine, expire_on_commit=False)
        self.replay = ReplayCache()
        self._embedder: Any = None
        self.faults: dict[str, str] = {}
        if self.settings.agent_fault_injection:
            self.faults = json.loads(os.environ.get("OPSPILOT_MCP_FAULTS", "{}") or "{}")

    def embedder(self) -> Any:
        if self._embedder is None:
            from opspilot.llm.factory import build_embedding_or_degraded

            self._embedder = build_embedding_or_degraded(self.settings)
        return self._embedder

    # ------------------------------------------------------------------ core
    def run_tool(self, ctx: Context, tool: str, args: BaseModel, fn: ImplFn) -> BaseModel:
        t0 = time.perf_counter()
        rc = ctx.request_context
        raw = dict((rc.params or {}).get("arguments") or {})
        meta = dict(rc.meta or {})
        audit: dict[str, Any] = {
            "args_summary": json.loads(redact(json.dumps(_summary(raw, 120), ensure_ascii=False))),
            "args_sha256": args_sha256(raw),
        }
        try:
            caller = verify(
                self._secret,
                meta.get(META_KEY),
                server=self.server,
                tool=tool,
                args=raw,
                replay=self.replay,
            )
        except CallerError as exc:
            st = "replay" if exc.reason == "replayed_request" else "denied"
            self._audit(tool, st, t0, error_class="permission", error=exc.reason, **audit)
            raise MCPToolError(f"[permission] {exc.reason}") from None
        audit |= {
            "user_id": caller.user_id,
            "thread_id": caller.thread_id,
            "run_id": caller.run_id,
            "call_id": caller.call_id,
            "jti": caller.jti,
        }
        try:
            self._fault(tool)
            with self.sf() as s:
                user = _load_user(s, caller.user_id)
                audit["user_role"] = user.role
                tctx = ToolContext(
                    settings=self.settings,
                    session_factory=self.sf,
                    embedder=self.embedder() if self.server == "knowledge" else None,
                    run_id=caller.run_id,
                    thread_id=caller.thread_id or "",
                    user_id=caller.user_id,
                    allowed=[tool],
                )
                out = fn(s, user, args, tctx)
            size = len(out.model_dump_json().encode())
            if size > self.settings.mcp_max_output_bytes:
                self._audit(
                    tool,
                    "error",
                    t0,
                    error_class="upstream",
                    error="output_too_large",
                    output_bytes=size,
                    **audit,
                )
                raise MCPToolError("[upstream] output exceeds size limit")
            key = getattr(out, "idempotency_key", None)
            self._audit(tool, "ok", t0, output_bytes=size, idempotency_key=key, **audit)
            return out
        except MCPToolError:
            raise
        except ToolPermissionError as exc:
            self._audit(tool, "denied", t0, error_class="permission", error=str(exc), **audit)
            raise MCPToolError(f"[permission] {exc}") from None
        except ToolNotFound as exc:
            self._audit(tool, "error", t0, error_class="not_found", error=str(exc), **audit)
            raise MCPToolError(f"[not_found] {exc}") from None
        except self._conflict as exc:
            self._audit(tool, "error", t0, error_class="conflict", error=str(exc), **audit)
            raise MCPToolError(f"[conflict] {exc}") from None
        except (ToolTransientError, OperationalError) as exc:
            self._audit(
                tool, "error", t0, error_class="transient", error=type(exc).__name__, **audit
            )
            raise MCPToolError("[transient] temporary failure, retry later") from None
        except Exception as exc:  # never leak internals to the model
            log.error(
                "mcp_tool_internal_error",
                server=self.server,
                tool=tool,
                error=redact(repr(exc))[:300],
            )
            self._audit(
                tool, "error", t0, error_class="internal", error=type(exc).__name__, **audit
            )
            raise MCPToolError("[internal] tool failed") from None

    def _fault(self, tool: str) -> None:
        f = self.faults.get(tool)
        if not f:
            return
        if f.startswith("delay:"):
            time.sleep(float(f.split(":", 1)[1]))
        elif f == "error":
            raise ToolTransientError("injected transient failure")
        elif f == "crash":
            os._exit(3)  # simulate a dying server process

    def _audit(self, tool: str, status: str, t0: float, **kw: Any) -> None:
        try:
            with self.sf() as s:
                uid = kw.pop("user_id", None)
                s.add(
                    McpToolAudit(
                        side="server",
                        server=self.server,
                        tool=tool,
                        transport=self.transport,
                        status=status,
                        duration_ms=int((time.perf_counter() - t0) * 1000),
                        user_id=uuid.UUID(uid) if uid else None,
                        error=redact(str(kw.pop("error", "") or ""))[:500] or None,
                        **{k: v for k, v in kw.items() if v is not None},
                    )
                )
                s.commit()
        except Exception as exc:  # audit must not break the tool, but is logged loudly
            log.error(
                "mcp_audit_write_failed", server=self.server, tool=tool, error=type(exc).__name__
            )


# ---------------------------------------------------------------------- entrypoint
def serve(build: Callable[[Runtime], MCPServer], name: str, argv: list[str] | None = None) -> None:
    """`python -m opspilot.mcp.servers.<name> [--transport stdio|http] [--port N]`.

    HTTP (optional): binds 127.0.0.1 only, requires `Authorization: Bearer <token>` where the
    token is OPSPILOT_MCP_HTTP_TOKEN, enables DNS-rebinding protection (Host allowlist) and
    rejects any Origin not in OPSPILOT_MCP_HTTP_ALLOWED_ORIGINS (default: none)."""
    ap = argparse.ArgumentParser(prog=f"opspilot-mcp-{name}")
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args(argv)
    _configure_logging()
    rt = Runtime(name, "stdio" if a.transport == "stdio" else "http")
    srv = build(rt)
    if a.transport == "stdio":
        srv.run("stdio")
        return
    from opspilot.mcp.http_guard import run_http

    run_http(srv, rt, host=a.host, port=a.port)
