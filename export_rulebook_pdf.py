from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def _pick_font() -> str:
    candidates = [
        ("C:/Windows/Fonts/calibri.ttf", "Calibri"),
        ("C:/Windows/Fonts/arial.ttf", "Arial"),
    ]
    for path, name in candidates:
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont(name, path))
            return name
    return "Helvetica"


def export_pdf(md_path: Path, pdf_path: Path) -> None:
    font_name = _pick_font()
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    page_width, page_height = A4
    left = 48
    top = page_height - 48
    bottom = 48
    line_h = 14
    max_width = page_width - (left * 2)

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    y = top

    def new_page() -> None:
        nonlocal y
        c.showPage()
        c.setFont(font_name, 10)
        y = top

    c.setFont(font_name, 10)
    for raw_line in lines:
        line = raw_line.replace("\t", "    ")
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

        words = content.split(" ")
        if not words:
            wrapped = [""]
        else:
            wrapped = []
            current = ""
            for w in words:
                tentative = w if not current else f"{current} {w}"
                if pdfmetrics.stringWidth(tentative, font_name, size) <= max_width:
                    current = tentative
                else:
                    if current:
                        wrapped.append(current)
                    current = w
            if current:
                wrapped.append(current)

        for out in wrapped:
            if y <= bottom:
                new_page()
                c.setFont(font_name, size)
            c.drawString(left, y, out)
            y -= line_h

        if line.startswith("#"):
            y -= 4

    c.save()


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    export_pdf(
        root / "Trading_Signal_Rulebook.md",
        root / "Trading_Signal_Rulebook.pdf",
    )
