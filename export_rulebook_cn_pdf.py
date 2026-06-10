from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


def _char_wrap(line: str, font_name: str, font_size: int, max_width: float) -> list[str]:
    if not line:
        return [""]
    out: list[str] = []
    current = ""
    for ch in line:
        tentative = f"{current}{ch}"
        if pdfmetrics.stringWidth(tentative, font_name, font_size) <= max_width:
            current = tentative
        else:
            if current:
                out.append(current)
            current = ch
    if current:
        out.append(current)
    return out if out else [""]


def export_pdf(md_path: Path, pdf_path: Path) -> None:
    font_name = "STSong-Light"
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))

    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    page_width, page_height = A4
    left = 44
    top = page_height - 44
    bottom = 44
    line_h = 15
    max_width = page_width - left * 2

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    y = top

    def new_page() -> None:
        nonlocal y
        c.showPage()
        y = top

    for raw in lines:
        line = raw.replace("\t", "    ")
        if line.startswith("# "):
            size = 16
            content = line[2:].strip()
        elif line.startswith("## "):
            size = 13
            content = line[3:].strip()
        elif line.startswith("### "):
            size = 11
            content = line[4:].strip()
        else:
            size = 10
            content = line

        c.setFont(font_name, size)
        wrapped = _char_wrap(content, font_name, size, max_width)
        for out_line in wrapped:
            if y <= bottom:
                new_page()
                c.setFont(font_name, size)
            c.drawString(left, y, out_line)
            y -= line_h

        if line.startswith("#"):
            y -= 3

    c.save()


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    export_pdf(
        root / "Trading_Signal_Rulebook_CN.md",
        root / "Trading_Signal_Rulebook_CN.pdf",
    )
