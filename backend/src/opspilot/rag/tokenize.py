"""Chinese/English tokenizer shared by indexing and querying (must stay symmetric).

- Code-like identifiers (ERR_PAY_TIMEOUT, ORD-5003, PaymentAdapter.charge, v2.3.1) are kept
  whole and normalised to lowercase alphanumerics ("errpaytimeout", "ord5003") plus their
  parts, because PostgreSQL's parser would otherwise split them on '_' / '-' / '.'.
- Remaining text is segmented with jieba (search mode: emits sub-words for recall) using a
  small domain user dictionary (rag/data/userdict.txt).
- Output tokens are lowercase, stop words and punctuation removed.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import jieba

jieba.setLogLevel(logging.WARNING)

_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_\-.][A-Za-z0-9]+)+|[A-Za-z]+\d+[A-Za-z0-9]*")
_WORD_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")
_STOP = frozenset(
    [
        "的",
        "了",
        "和",
        "与",
        "及",
        "或",
        "是",
        "在",
        "有",
        "为",
        "对",
        "把",
        "被",
        "就",
        "都",
        "而",
        "也",
        "还",
        "又",
        "吗",
        "呢",
        "吧",
        "啊",
        "如何",
        "怎么",
        "怎样",
        "什么",
        "哪些",
        "哪个",
        "为什么",
        "请问",
        "一下",
        "我们",
        "你们",
        "他们",
        "这个",
        "那个",
        "这些",
        "那些",
        "可以",
        "需要",
        "应该",
        "是否",
        "一个",
        "进行",
        "时候",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "be",
        "with",
        "how",
        "what",
        "why",
        "which",
        "do",
        "does",
        "can",
        "i",
        "we",
        "you",
    ]
)


@lru_cache(maxsize=1)
def _init() -> None:
    jieba.load_userdict(str(Path(__file__).with_name("data") / "userdict.txt"))


def _norm_code(code: str) -> str:
    return re.sub(r"[^a-z0-9]", "", code.lower())


def tokenize(text: str) -> list[str]:
    _init()
    tokens: list[str] = []
    for m in _CODE_RE.finditer(text):
        whole = _norm_code(m.group())
        tokens.append(whole)
        tokens.extend(p.lower() for p in re.split(r"[_\-.]", m.group()) if p)
    rest = _CODE_RE.sub(" ", text)
    for seg in _WORD_RE.findall(rest):
        if seg.isascii():
            tokens.append(seg.lower())
        else:
            tokens.extend(jieba.cut_for_search(seg))
    return [t for t in tokens if t and t not in _STOP and not (len(t) == 1 and t.isascii())]


def to_search_text(text: str) -> str:
    """Space-joined tokens stored in chunks.search_text (tsv = to_tsvector('simple', ...))."""
    return " ".join(tokenize(text))


def query_tokens(text: str) -> list[str]:
    """De-duplicated query tokens, order preserved."""
    return list(dict.fromkeys(tokenize(text)))
