"""Caller identity for MCP tool calls.

The LLM never supplies identity. For every call the backend mints a short-lived HS256
"caller token" and sends it in the request `_meta` (key META_KEY), out of band from the tool
arguments the model produced. The token binds:

  sub  acting user id (the server re-loads the user + role from the DB; role in the token is
       informational only)
  aud  the MCP server name           -> a token for `knowledge` is useless at `ticket`
  tool the tool name                 -> cannot be reused for another tool
  ash  sha256 of the canonical args  -> parameter tampering after minting is detected
  thr / run / cid  thread, run, model tool-call id (audit)
  jti  unique id                     -> the server rejects replays within the token lifetime
  exp  now + mcp_caller_token_ttl_s

The signing key is derived from OPSPILOT_JWT_SECRET (HMAC, domain-separated) and is the only
secret handed to MCP server subprocesses (OPSPILOT_MCP_CALLER_SECRET). They never receive the
JWT secret, database admin credentials beyond the app URL, or any LLM API key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt

META_KEY = "opspilot.local/caller"
_ALG = "HS256"
_ISS = "opspilot-backend"


def derive_caller_secret(jwt_secret: str) -> str:
    return hmac.new(jwt_secret.encode(), b"opspilot-mcp-caller-v1", hashlib.sha256).hexdigest()


def args_sha256(args: dict[str, Any]) -> str:
    canon = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


@dataclass(frozen=True)
class Caller:
    user_id: str
    role: str
    thread_id: str | None
    run_id: str | None
    call_id: str | None
    jti: str
    server: str
    tool: str


def mint(
    secret: str,
    *,
    server: str,
    tool: str,
    args: dict[str, Any],
    user_id: str,
    role: str,
    thread_id: str | None,
    run_id: str | None,
    call_id: str | None,
    ttl_s: int,
) -> str:
    now = int(time.time())
    claims = {
        "iss": _ISS,
        "aud": f"mcp:{server}",
        "sub": user_id,
        "role": role,
        "tool": tool,
        "ash": args_sha256(args),
        "thr": thread_id,
        "run": run_id,
        "cid": call_id,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(claims, secret, algorithm=_ALG)


class CallerError(Exception):
    """Identity check failed (missing / invalid / expired / replayed / tampered)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReplayCache:
    """jti -> exp; a jti is accepted once. Bounded by token lifetime (entries expire)."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, jti: str, exp: float) -> bool:
        now = time.time()
        with self._lock:
            if len(self._seen) > 10_000:
                self._seen = {k: v for k, v in self._seen.items() if v > now}
            if jti in self._seen and self._seen[jti] > now - 60:
                return False
            self._seen[jti] = exp
            return True


def verify(
    secret: str,
    token: str | None,
    *,
    server: str,
    tool: str,
    args: dict[str, Any],
    replay: ReplayCache,
) -> Caller:
    if not token or not isinstance(token, str):
        raise CallerError("missing_caller_token")
    try:
        c = jwt.decode(
            token,
            secret,
            algorithms=[_ALG],
            audience=f"mcp:{server}",
            issuer=_ISS,
            leeway=5,
            options={"require": ["exp", "iat", "sub", "jti", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise CallerError("caller_token_expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise CallerError("caller_token_wrong_server") from exc
    except jwt.PyJWTError as exc:
        raise CallerError("caller_token_invalid") from exc
    if c.get("tool") != tool:
        raise CallerError("caller_token_wrong_tool")
    if not hmac.compare_digest(str(c.get("ash")), args_sha256(args)):
        raise CallerError("arguments_tampered")
    if not replay.claim(str(c["jti"]), float(c["exp"])):
        raise CallerError("replayed_request")
    return Caller(
        user_id=str(c["sub"]),
        role=str(c.get("role") or "user"),
        thread_id=c.get("thr"),
        run_id=c.get("run"),
        call_id=c.get("cid"),
        jti=str(c["jti"]),
        server=server,
        tool=tool,
    )
