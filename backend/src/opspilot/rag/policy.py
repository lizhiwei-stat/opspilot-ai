"""Answer-side policy checks: defence layers that do NOT rely on the injection regex.

Prompt-injection defence in OpsPilot is layered (docs/STAGE2_5_EVAL.md §injection):
  1. ACL in SQL: retrieved text can never come from a KB the user cannot read, whatever the
     query or a document says.
  2. Structural isolation: retrieved text is data. It is wrapped in <untrusted_document>
     (angle brackets neutralised) for LLMs; the mock generator only *quotes* source sentences
     and never executes anything.
  3. Heuristic detection (rag/sanitize.py regex): flags + strips instruction-like lines.
  4. Citation verification (this module): every quote must occur verbatim in the chunk it
     cites; citations to unknown ids are dropped.
  5. Output policy (this module): the answer may not contain contact/exfiltration targets
     (e-mail, URL, phone) or secret-looking strings that are absent from the trusted
     (non-flagged) evidence, nor claim to have performed actions (create/close/approve...).
     Violations replace the answer with INSUFFICIENT and are reported.
  6. No tools are reachable from QA; Stage 3 tool calls need server-side human approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s，。）)>\]]+", re.I)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|eyJ[\w-]{20,}\.[\w-]+\.[\w-]+)\b")
_ACTION_CLAIM = re.compile(
    r"(我|系统|助手)?(已|已经)(为你|帮你)?(创建|关闭|删除|批准|提交|发送|重置|转账)(了)?"
)
_WS = re.compile(r"\s+")


@dataclass
class PolicyResult:
    ok: bool
    violations: list[str] = field(default_factory=list)


def norm(s: str) -> str:
    return _WS.sub("", s)


def verify_quote(quote: str, source: str) -> bool:
    """A citation quote must be a verbatim (whitespace-insensitive) substring of its chunk."""
    q = norm(quote)
    return bool(q) and q in norm(source)


def check_answer(answer: str, trusted_evidence: list[str]) -> PolicyResult:
    trusted = "\n".join(trusted_evidence)
    v: list[str] = []
    for name, rx in (("email", _EMAIL), ("url", _URL), ("phone", _PHONE)):
        for m in rx.findall(answer):
            if m not in trusted:
                v.append(f"untrusted_{name}:{m[:40]}")
    if _SECRET.search(answer):
        v.append("secret_like_string")
    if _ACTION_CLAIM.search(answer):
        v.append("action_claim")
    return PolicyResult(not v, v)
