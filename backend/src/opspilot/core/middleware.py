"""Request-id + trace-id + access-log middleware (pure ASGI, no BaseHTTPMiddleware overhead)."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from opspilot.core.logging import get_logger
from opspilot.core.tracing import bind_trace, record_span

log = get_logger("opspilot.access")
_VALID_RID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _VALID_RID.match(incoming) else uuid.uuid4().hex
        tin = headers.get(b"x-trace-id", b"").decode("latin-1")
        structlog.contextvars.clear_contextvars()
        tid = bind_trace(request_id=rid, trace_id=tin if _VALID_RID.match(tin) else None)
        start = time.perf_counter()
        status = {"code": 500}

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                hdrs = list(message.get("headers") or [])
                hdrs.append((b"x-request-id", rid.encode()))
                hdrs.append((b"x-trace-id", tid.encode()))
                message["headers"] = hdrs
            await send(message)

        err_type = None
        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            err_type = type(exc).__name__
            raise
        finally:
            dur = round((time.perf_counter() - start) * 1000, 2)
            code = status["code"]
            log.info(
                "http_request",
                method=scope["method"],
                path=scope["path"],
                status=code,
                duration_ms=dur,
            )
            if not scope["path"].startswith("/metrics"):
                record_span(
                    name=f"{scope['method']} {scope['path']}",
                    kind="http",
                    trace_id=tid,
                    request_id=rid,
                    status=str(code),
                    duration_ms=dur,
                    error_type=err_type,
                    attrs={"method": scope["method"], "path": scope["path"]},
                )
