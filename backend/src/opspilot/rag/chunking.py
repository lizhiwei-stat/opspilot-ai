"""Chunking: Markdown heading-aware sections, then LangChain RecursiveCharacterTextSplitter
with Chinese + English separators. Offsets are exact (content == source[char_start:char_end]).

Offsets are relative to the page text for PDFs and to the whole cleaned text otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from opspilot.rag.parsers import ParsedDocument

SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", "，", ", ", " ", ""]
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)


@dataclass(frozen=True)
class ChunkDraft:
    index: int
    content: str
    page_number: int | None
    heading_path: str | None
    char_start: int
    char_end: int


def _sections(text: str, markdown: bool) -> list[tuple[int, str, str | None]]:
    """(offset, section_text, heading_path) blocks."""
    if not markdown:
        return [(0, text, None)]
    heads = list(_HEADING.finditer(text))
    if not heads:
        return [(0, text, None)]
    out: list[tuple[int, str, str | None]] = []
    if heads[0].start() > 0:
        out.append((0, text[: heads[0].start()], None))
    stack: list[tuple[int, str]] = []
    for i, h in enumerate(heads):
        level, title = len(h.group(1)), h.group(2).strip()
        stack = [s for s in stack if s[0] < level] + [(level, title)]
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((h.start(), text[h.start() : end], " > ".join(t for _, t in stack)))
    return out


def chunk_document(doc: ParsedDocument, *, chunk_size: int, overlap: int) -> list[ChunkDraft]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=SEPARATORS,
        keep_separator="end",
        strip_whitespace=False,
        add_start_index=True,
    )
    drafts: list[ChunkDraft] = []
    for page in doc.pages:
        for sec_off, sec_text, heading in _sections(page.text, doc.kind == "markdown"):
            for d in splitter.create_documents([sec_text]):
                raw = d.page_content
                lead = len(raw) - len(raw.lstrip())
                content = raw.strip()
                if not content:
                    continue
                if _HEADING.fullmatch(content):
                    # a bare heading split off its body carries no information of its own;
                    # its words still reach the index via heading_path (search_text/embed header)
                    continue
                start = sec_off + d.metadata["start_index"] + lead
                end = start + len(content)
                assert page.text[start:end] == content  # noqa: S101 - invariant, tested
                drafts.append(
                    ChunkDraft(len(drafts), content, page.page_number, heading, start, end)
                )
    return drafts
