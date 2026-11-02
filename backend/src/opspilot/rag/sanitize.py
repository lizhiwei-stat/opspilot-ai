"""Retrieved text is UNTRUSTED input. We (1) flag instruction-like lines, (2) remove them
from anything shown to / generated for the user, (3) wrap context in explicit delimiters for
real LLMs. Detection is heuristic and documented as such (STAGE2_DESIGN.md §9)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS = [
    r"忽略(之前|以上|上面|前面|所有|先前)?.{0,6}(指令|指示|规则|要求|提示)",
    r"(无视|不要遵守|绕过).{0,8}(指令|规则|限制|审批|系统)",
    r"(你现在是|你将扮演|从现在开始你)",
    r"(系统提示词|system\s*prompt|developer\s*message)",
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+(instructions|prompts|rules)",
    r"disregard\s+(the\s+)?(previous|above|system)",
    r"you\s+are\s+now\b",
    r"(直接|立即|自动)(创建|关闭|删除|批准).{0,6}(工单|审批|数据)",
    r"(调用|执行)\s*(工具|函数|命令)\s*[:：]",
    r"</?\s*(system|assistant|document|untrusted)[^>]*>",
    r"(发送|发给|提供|泄露|告诉).{0,12}(管理员)?(密码|密钥|口令|令牌|token|api\s*key)",
    r"(密码|密钥|口令|令牌|token|api\s*key).{0,6}(发送|发给|发到|提供给|告诉)",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I)


@dataclass(frozen=True)
class SanitizedText:
    text: str
    flagged: bool
    removed_lines: list[str]


def sanitize(text: str) -> SanitizedText:
    kept, removed = [], []
    for line in text.split("\n"):
        (removed if _INJECTION.search(line) else kept).append(line)
    return SanitizedText("\n".join(kept).strip(), bool(removed), removed)


def is_suspicious(text: str) -> bool:
    return bool(_INJECTION.search(text))


def wrap_untrusted(idx: int, source: str, text: str) -> str:
    """Delimiter for LLM prompts; angle brackets in content are neutralised first."""
    safe = text.replace("<", "‹").replace(">", "›")
    return f'<untrusted_document id="{idx}" source="{source}">\n{safe}\n</untrusted_document>'
