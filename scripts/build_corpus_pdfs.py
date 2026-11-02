"""Render evals/corpus/_pdf_src/*.md into multi-page PDFs (one '## 第 N 页' section per page).

The generated PDFs are committed, so this only needs re-running when the sources change.
Requires a CJK font; pass --font or set OPSPILOT_CJK_FONT (default: Noto Sans CJK on Debian/Ubuntu).
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "evals" / "corpus" / "_pdf_src"
TARGETS = {
    "deployment-rollback-guide.md": "order-platform",
    "payment-gateway-integration.md": "order-platform",
    "monitoring-alerting-guide.md": "public-faq",
}
DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def render(src: Path, dst: Path, font: str) -> int:
    text = src.read_text(encoding="utf-8")
    title = text.splitlines()[0].lstrip("# ").strip()
    pages = re.split(r"^## 第 \d+ 页：", text, flags=re.M)[1:]
    pdf = FPDF(format="A4")
    pdf.set_creator("opspilot-synthetic-corpus")
    pdf.set_title(title)
    pdf.set_creation_date(__import__("datetime").datetime(2026, 11, 2))
    pdf.add_font("cjk", "", font, collection_font_number=2 if font.endswith(".ttc") else 0)
    for body in pages:
        heading, _, rest = body.partition("\n")
        pdf.add_page()
        pdf.set_font("cjk", size=15)
        pdf.multi_cell(0, 9, f"{title} - {heading.strip()}")
        pdf.ln(3)
        pdf.set_font("cjk", size=11)
        for para in [p.strip() for p in rest.strip().split("\n") if p.strip()]:
            pdf.multi_cell(0, 7, para)
            pdf.ln(2)
    dst.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(dst))
    return len(pages)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", default=os.environ.get("OPSPILOT_CJK_FONT", DEFAULT_FONT))
    args = ap.parse_args()
    for name, kb in TARGETS.items():
        dst = ROOT / "evals" / "corpus" / kb / name.replace(".md", ".pdf")
        n = render(SRC / name, dst, args.font)
        print(f"{dst.relative_to(ROOT)}: {n} pages")


if __name__ == "__main__":
    main()
