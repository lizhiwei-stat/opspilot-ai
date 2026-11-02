"""Value-level secret redaction for logs, ledgers and generated reports.

Key-name redaction (password=..., api_key=...) lives in core/logging.py. This module also
scrubs secret *values* that appear inside free text: well-known key shapes and any value
registered at startup (e.g. the configured OPSPILOT_LLM_API_KEY / DASHSCOPE_API_KEY), so an
exception message or URL that happens to embed a key is still masked.
"""

from __future__ import annotations

import os
import re

_SHAPES = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._\-]{16,}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,})"
)
_KNOWN: set[str] = set()
_ENV_KEYS = ("OPSPILOT_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")


def register_secret(value: str | None) -> None:
    if value and len(value) >= 8:
        _KNOWN.add(value)


def _env_secrets() -> set[str]:
    return {v for k in _ENV_KEYS if (v := os.environ.get(k)) and len(v) >= 8}


def redact(text: str) -> str:
    out = text
    for v in sorted(_KNOWN | _env_secrets(), key=len, reverse=True):
        out = out.replace(v, "***REDACTED***")
    return _SHAPES.sub("***REDACTED***", out)
