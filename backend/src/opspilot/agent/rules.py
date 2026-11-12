"""Deterministic rules shared by the mock model and the server-side guards.

These are NOT a substitute for the model in real mode: in Qwen mode the model classifies and
plans, but the server still applies the guards here (e.g. a side-effecting tool is only
allowed when the USER's own message explicitly asks for it - tool output can never unlock it).
"""

from __future__ import annotations

import re

import jieba

# lookarounds instead of \b: in "报ORD-5003" the CJK char and "O" are both \w (no boundary)
_L, _R = r"(?<![A-Za-z0-9_])", r"(?![A-Za-z0-9_])"
ERROR_CODE = re.compile(_L + r"(?:(?!TCK-)[A-Z]{2,6}-\d{3,5}|ERR_[A-Z_]{3,40})" + _R)
TICKET_NO = re.compile(_L + r"TCK-\d{4}" + _R)
_LOG_WORDS = re.compile(
    r"日志|log|trace|报错记录|错误记录|最近.{0,6}(错误|报错|异常)|告警记录", re.I
)
_TROUBLE = re.compile(r"故障|报错|异常|排查|500|5xx|超时|失败|挂了|不可用|告警|错误码")
_TICKET_ACTION = re.compile(
    r"(创建|新建|提交|生成|开|提|建)(一个|一张|个|张)?"
    r"(?:[^。？?！!，,\n]{0,12})?"
    r"(待审(?:核|批)?|待确认)?的?(工单|ticket)(?:草稿)?",
    re.I,
)
_TICKET_QUERY = re.compile(r"(查|查看|看看|进展|状态).{0,6}(工单)|工单.{0,6}(进展|状态)")
_CHITCHAT = re.compile(r"^(你好|您好|hi|hello|谢谢|多谢|早上好|晚上好)[!！。.\s]*$", re.I)
_OUT_OF_SCOPE = re.compile(r"天气|股票|股价|彩票|写(一首)?诗|讲个笑话|菜谱|足球|火星|员工绩效|绩效评级")

SERVICES = {
    "order-service": ("订单", "order"),
    "payment-gateway": ("支付", "payment", "pay"),
    "inventory-service": ("库存", "inventory"),
    "auth-service": ("登录", "认证", "token", "令牌", "auth"),
}


def services_in(text: str) -> list[str]:
    t = text.lower()
    return [s for s, kws in SERVICES.items() if any(k in t for k in kws)]


def entities(text: str) -> dict[str, list[str]]:
    return {
        "error_codes": sorted(set(ERROR_CODE.findall(text))),
        "tickets": sorted(set(TICKET_NO.findall(text))),
        "services": services_in(text),
    }


def wants_logs(text: str) -> bool:
    return bool(_LOG_WORDS.search(text))


def explicit_ticket_request(text: str) -> bool:
    """Only the user's own message can authorise the draft tool."""
    for m in _TICKET_ACTION.finditer(text):
        prefix = text[max(0, m.start() - 4) : m.start()]
        if re.search(r"下一轮|下一次|下次|稍后$", prefix):
            continue  # deferred ("next turn") is not a request on this turn
        return True
    return False


def wants_service_status(text: str) -> bool:
    """User is asking for a runtime service status, not a ticket/KB how-to."""
    if TICKET_NO.search(text) or _TICKET_QUERY.search(text):
        return False
    if re.search(r"怎么|如何|怎样|步骤", text):
        return False  # "如何查看服务状态" is a runbook question, not a live probe
    if re.search(r"服务状态|运行状态|service status", text, re.I):
        return True
    return bool(services_in(text)) and bool(re.search(r"(查|查看|看看).{0,20}状态", text))


_INCIDENT = re.compile(r"日志|文档|分析|诊断|排查|根因|关联")


def rule_intent(text: str) -> str:
    e = entities(text)
    if explicit_ticket_request(text):
        # Stage 4: "diagnose, then file a ticket" -> full pipeline (RAG + logs + error code
        # + solution) before the draft; a bare "create a ticket" stays ticket_action
        if e["error_codes"] or _INCIDENT.search(text):
            return "incident_ticket"
        return "ticket_action"
    if e["tickets"] or _TICKET_QUERY.search(text):
        return "ticket_query"
    if _CHITCHAT.search(text.strip()):
        return "chitchat"
    if _OUT_OF_SCOPE.search(text):
        return "out_of_scope"
    if e["error_codes"] or wants_logs(text) or _TROUBLE.search(text):
        return "troubleshoot"
    return "knowledge_qa"


# ------------------------------------------------------------ answer-slot analysis
# A "specific-fact" question asks for one concrete value (a number, duration, a named
# entity, a choice between named options, or a yes/no on a policy). For those, the concept
# being asked about (the head term) must exist in the knowledge the user can read; topical
# similarity alone is not evidence (Stage 2.5 held-out finding: h029/h032/h033/h035/h036).
_Q_BEFORE = re.compile(r"(多少|几[天个次步台秒分钟小时]?|多久|多长时间|多长|是什么|啥)")
_Q_VALUE = re.compile(r"(多少|几[天个次步台秒分钟小时]?|多久|多长时间|多长)")
_Q_WHAT = re.compile(r"(是什么|啥)")
_Q_AFTER = re.compile(r"(哪家|哪个|哪种|哪款|哪一个)")
_YESNO = re.compile(r"(可以|能|能否|允许|支持|会)([\u4e00-\u9fff]{1,4})吗")
_CHOICE = re.compile(r"([A-Za-z\u4e00-\u9fff]{2,12})\s*还是\s*([A-Za-z\u4e00-\u9fff]{2,12})")
_GENERIC = frozenset(
    [
        "的",
        "是",
        "用",
        "了",
        "要",
        "在",
        "有",
        "和",
        "一",
        "一次",
        "一下",
        "我",
        "你",
        "他",
        "它",
        "这",
        "那",
        "吗",
        "呢",
        "啊",
        "可以",
        "能",
        "什么",
        "怎么",
        "如何",
        "哪",
        "服务",
        "系统",
        "问题",
        "情况",
        "时候",
        "默认",
        "一般",
        "通常",
        "大概",
        "具体",
        "现在",
        "目前",
        "请问",
        "用的",
        "设置",
        "建议",
        "换",
        "改",
        "调",
        "处理",
        "发生",
        "进行",
        "东西",
        "多少",
        "多久",
        "几",
        "天",
        "次",
        "个",
        "步",
    ]
)


def _content_tokens(text: str) -> list[str]:
    return [
        t
        for t in jieba.lcut(text)
        if len(t.strip()) >= 2 and t not in _GENERIC and not re.fullmatch(r"\W+", t)
    ]


_SPLIT = re.compile(r"[的是有用在为把被及和与，,。？?！!\s]+")


def _head(segment: str, last: bool = True) -> list[str]:
    """Head term of a phrase: the last (or first) particle-delimited piece if short,
    else its last (first) content token."""
    parts = [p for p in _SPLIT.split(segment) if p]
    if not parts:
        return []
    piece = parts[-1] if last else parts[0]
    if len(piece) <= 5:
        return [piece] if piece not in _GENERIC else []
    toks = _content_tokens(piece)
    return toks[-1:] if last else toks[:1]


_DROPPABLE = frozenset("分跑能要会各里上下中了过着每该才就可吧也都再还最")


def head_parts(head: str) -> list[str]:
    """Matching units for a head term. Boundary particles/verbs are dropped; if the rest
    splits into parts of >= 2 chars they must co-occur in one chunk, otherwise the whole term
    must occur (e.g. 商户号 -> 商户+号: '号' is a noun suffix, so plain '商户' does not count)."""
    toks = [t for t in jieba.lcut(head) if t.strip() and t not in _DROPPABLE]
    toks = [t for t in toks if t not in _GENERIC] or toks
    if not toks:
        return []
    if len(toks) > 1 and all(len(t) >= 2 for t in toks):
        return toks
    return ["".join(toks)]


def fact_question(text: str) -> dict[str, object] | None:
    """Return {"type", "heads"} for specific-fact questions, else None (procedural/open
    questions such as 怎么/如何/为什么/该先检查哪里 are judged by retrieval + generation)."""
    # follow-up pronouns ("它现在是什么优先级") are not part of the asked-for concept
    text = re.sub(r"^(它|这个工单|那个工单|这个|那个|该工单|该)(现在|目前)?", "", text.strip())
    if re.search(r"怎么|如何|为什么|为何|怎样|步骤|哪里|原因", text):
        return None
    if ERROR_CODE.search(text):  # error-code questions are handled by analyze_error_code
        return None
    m = _CHOICE.search(text)
    if m:
        return {
            "type": "choice",
            "heads": [
                _head(m.group(1))[-1:] or [m.group(1)],
                _head(m.group(2), last=False)[:1] or [m.group(2)],
            ][0]
            + (_head(m.group(2), last=False)[:1] or [m.group(2)]),
        }
    m = _YESNO.search(text)
    if m:
        return {"type": "yesno", "heads": [m.group(2)]}
    m = _Q_AFTER.search(text)
    if m:
        h = _head(text[m.end() :], last=False)
        return {"type": "entity", "heads": h} if h else None
    m = _Q_VALUE.search(text)
    if m:
        h = _head(text[: m.start()])
        return {"type": "value", "heads": h} if h else None
    m = _Q_WHAT.search(text)
    if m:
        h = _head(text[: m.start()])
        return {"type": "entity", "heads": h} if h else None
    return None
