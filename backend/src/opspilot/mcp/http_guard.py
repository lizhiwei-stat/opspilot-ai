"""Optional Streamable HTTP transport for the MCP servers, locked down:

* bind: loopback only (127.0.0.1 / ::1 / localhost); anything else is refused at startup
* authn: `Authorization: Bearer <OPSPILOT_MCP_HTTP_TOKEN>` (>= 32 chars, constant-time compare)
  -> 401 otherwise. This authenticates the *client process*; the acting user is still
  carried per call by the signed caller token in `_meta` (identity.py)
* authz: per-tool ACL inside the server (same as stdio)
* Origin: requests carrying an Origin header not in OPSPILOT_MCP_HTTP_ALLOWED_ORIGINS -> 403
  (browsers always send Origin; the backend client sends none)
* Host: SDK DNS-rebinding protection with an explicit Host allowlist
* body size: SDK limit (64 KiB here)
"""

from __future__ import annotations

import hmac
import json
import os

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from opspilot.mcp.runtime import Runtime, log

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class GuardMiddleware:
    def __init__(self, app: ASGIApp, token: str, origins: set[str]) -> None:
        self.app, self.token, self.origins = app, token.encode(), origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            log.warning("mcp_http_origin_rejected", origin=origin[:100])
            await JSONResponse({"error": "origin not allowed"}, status_code=403)(
                scope, receive, send
            )
            return
        auth = headers.get("authorization", "")
        given = auth[7:].encode() if auth.lower().startswith("bearer ") else b""
        if not given or not hmac.compare_digest(given, self.token):
            await JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_app(srv: MCPServer, port: int) -> ASGIApp:
    token = os.environ.get("OPSPILOT_MCP_HTTP_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("OPSPILOT_MCP_HTTP_TOKEN (>=32 chars) is required for the HTTP transport")
    raw = os.environ.get("OPSPILOT_MCP_HTTP_ALLOWED_ORIGINS", "").strip()
    items = (
        json.loads(raw) if raw.startswith("[") else raw.split(",")
    )  # JSON list (Settings) or CSV
    origins = {str(o).strip() for o in items if str(o).strip()}
    sec = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"],
        allowed_origins=sorted(origins),
    )
    app = srv.streamable_http_app(
        transport_security=sec, max_request_body_size=64 * 1024, json_response=True
    )
    return GuardMiddleware(app, token, origins)


def run_http(srv: MCPServer, rt: Runtime, *, host: str, port: int) -> None:
    if host not in LOOPBACK:
        raise SystemExit(f"refusing to bind MCP HTTP transport to non-loopback host {host!r}")
    if not 1024 <= port <= 65535:
        raise SystemExit("--port 1024-65535 required for the HTTP transport")
    log.info("mcp_http_start", server=rt.server, host=host, port=port)
    uvicorn.run(build_app(srv, port), host=host, port=port, log_level="warning")
