"""Upload validation + parsing. Type is decided by content (magic bytes / decoding), not only
by the filename; every failure raises ParseError with a user-facing Chinese message."""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath

from pypdf import PdfReader
from pypdf.errors import PdfReadError

ALLOWED_EXT = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}
_TEXT_ENCODINGS = ("utf-8-sig", "gb18030")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ParseError(ValueError):
    """Validation/parse failure; .code is machine-readable, str() is shown to users."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedPage:
    page_number: int | None  # 1-based for PDF, None for text formats
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    mime_type: str
    kind: str  # pdf | markdown | text
    pages: list[ParsedPage]
    encoding: str | None

    @property
    def char_count(self) -> int:
        return sum(len(p.text) for p in self.pages)


def safe_filename(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name.strip()
    base = re.sub(r"[^\w.\-\u4e00-\u9fff ]", "_", base)[:200]
    if not base or base.startswith("."):
        raise ParseError("bad_filename", "文件名无效")
    return base


def _collapse_spaced_glyphs(line: str) -> str:
    """Undo per-glyph spacing some PDF producers emit ("回 滚 命 令" -> "回滚命令").

    Only applies when the line is dominated by single-character tokens; real word
    gaps in such lines appear as runs of >=2 spaces and are kept as one space.
    """
    tokens = line.split()
    if len(tokens) < 6 or sum(len(t) == 1 for t in tokens) / len(tokens) < 0.8:
        return line
    words = re.split(r" {2,}", line.strip())
    return " ".join(w.replace(" ", "") for w in words)


def _squeeze_inner_spaces(line: str) -> str:
    return re.sub(r"(?<=\S) {2,}(?=\S)", " ", line)


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = "\n".join(
        _squeeze_inner_spaces(_collapse_spaced_glyphs(line)).rstrip() for line in text.split("\n")
    )
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def validate_and_parse(filename: str, data: bytes, *, max_bytes: int) -> ParsedDocument:
    name = safe_filename(filename)
    ext = PurePath(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ParseError(
            "unsupported_type",
            f"不支持的文件类型 {ext or '(无扩展名)'}，仅支持 PDF / Markdown / TXT",
        )
    if not data:
        raise ParseError("empty_file", "文件为空")
    if len(data) > max_bytes:
        raise ParseError("too_large", f"文件过大：{len(data)} 字节，上限 {max_bytes} 字节")
    is_pdf = data[:5] == b"%PDF-"
    if ext == ".pdf":
        if not is_pdf:
            raise ParseError("content_mismatch", "扩展名为 .pdf，但文件内容不是 PDF")
        return _parse_pdf(data)
    if is_pdf:
        raise ParseError("content_mismatch", "文件内容是 PDF，但扩展名不是 .pdf")
    if b"\x00" in data[:8192]:
        raise ParseError("binary_content", "文件包含二进制内容，不是有效的文本文件")
    for enc in _TEXT_ENCODINGS:
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - gb18030 decodes almost anything; kept for safety
        raise ParseError("bad_encoding", "无法识别文本编码（支持 UTF-8 / GB18030）")
    text = clean_text(text)
    if not text:
        raise ParseError("empty_text", "文件没有可索引的文本内容")
    kind = "markdown" if ext in (".md", ".markdown") else "text"
    return ParsedDocument(
        ALLOWED_EXT[ext], kind, [ParsedPage(None, text)], "utf-8" if enc == "utf-8-sig" else enc
    )


_SENT_END = "。！？；：.!?;:）)」”"
_LIST_START = re.compile(r"^\s*(?:[-*•]|\d+[.、)]|[（(]\d+[)）]|#)")


def _width(line: str) -> int:
    """Approximate rendered width: CJK glyphs are ~2x a latin glyph."""
    return sum(2 if ord(c) > 0x2E7F else 1 for c in line)


def unwrap_pdf_lines(text: str) -> str:
    """Re-join visual line wraps from PDF text extraction.

    A line is merged into the previous one only when the previous *visual* line is
    "full" (>= 70% of the longest line on the page — short lines are headings or
    paragraph ends), does not end with sentence punctuation, and the next line does
    not look like a list item / heading. CJK boundaries join without a space.
    """
    lines = text.split("\n")
    full = 0.7 * max((_width(x) for x in lines), default=0)
    out: list[str] = []
    prev_visual = ""
    for line in lines:
        if (
            out
            and out[-1]
            and line.strip()
            and _width(prev_visual) >= full
            and out[-1][-1] not in _SENT_END
            and not _LIST_START.match(line)
        ):
            prev = out[-1]
            sep = "" if ord(prev[-1]) > 0x2E7F or ord(line.lstrip()[0]) > 0x2E7F else " "
            out[-1] = prev + sep + line.lstrip()
        else:
            out.append(line)
        prev_visual = line
    return "\n".join(out)


def _parse_pdf(data: bytes) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("pdf_encrypted", "PDF 已加密，无法解析")
        pages = [
            ParsedPage(i + 1, unwrap_pdf_lines(clean_text(p.extract_text() or "")))
            for i, p in enumerate(reader.pages)
        ]
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ParseError(
            "pdf_corrupt", f"PDF 文件损坏或格式不受支持：{type(exc).__name__}"
        ) from exc
    pages = [p for p in pages if p.text]
    if not pages:
        raise ParseError("pdf_no_text", "PDF 中没有可提取的文本（可能是扫描件，当前不支持 OCR）")
    return ParsedDocument("application/pdf", "pdf", pages, None)
