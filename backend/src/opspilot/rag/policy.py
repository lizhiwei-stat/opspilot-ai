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


def verify_quote(quote: str, source: str, sep: str = " … ") -> bool:
    """Every part of a citation quote must be a verbatim (whitespace-insensitive) substring
    of the chunk it cites."""
    src = norm(source)
    parts = [norm(p) for p in quote.split(sep)]
    return bool(parts) and all(p and p in src for p in parts)


def _host(url: str) -> str:
    u = re.sub(r"^(?:https?://)", "", url, flags=re.I)
    return u.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].lower()


def _allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in allowlist)


def check_answer(
    answer: str, trusted_evidence: list[str], link_allowlist: tuple[str, ...] = ()
) -> PolicyResult:
    """Links are allowed only if they occur in trusted evidence AND their host is on the
    allowlist: a regex-free injection inside an un-flagged document ("please append
    https://exfil...") is therefore still blocked, because evidence presence alone is not
    enough for a URL."""
    trusted = "\n".join(trusted_evidence)
    v: list[str] = []
    for name, rx in (("email", _EMAIL), ("url", _URL), ("phone", _PHONE)):
        for m in rx.findall(answer):
            if m not in trusted:
                v.append(f"untrusted_{name}:{m[:40]}")
            elif name == "url" and not _allowed(_host(m), link_allowlist):
                v.append(f"link_not_allowlisted:{_host(m)[:40]}")
    if _SECRET.search(answer):
        v.append("secret_like_string")
    if _ACTION_CLAIM.search(answer):
        v.append("action_claim")
    return PolicyResult(not v, v)
