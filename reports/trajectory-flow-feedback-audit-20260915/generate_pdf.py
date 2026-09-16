from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    TableStyle,
)

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "feedback_audit.md"
OUTPUT = HERE / "trajectory_flow_feedback_audit.pdf"
FONT_PATH = Path("C:/Windows/Fonts/malgun.ttf")
BOLD_PATH = Path("C:/Windows/Fonts/malgunbd.ttf")

pdfmetrics.registerFont(TTFont("Malgun", str(FONT_PATH)))
pdfmetrics.registerFont(TTFont("MalgunBold", str(BOLD_PATH)))
pdfmetrics.registerFontFamily("Malgun", normal="Malgun", bold="MalgunBold")

PAGE_W, PAGE_H = A4
MARGIN_X = 16 * mm
MARGIN_TOP = 17 * mm
MARGIN_BOTTOM = 16 * mm

styles = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "KTitle",
    parent=styles["Title"],
    fontName="MalgunBold",
    fontSize=20,
    leading=27,
    textColor=colors.HexColor("#172554"),
    alignment=TA_CENTER,
    spaceAfter=12,
)
H1 = ParagraphStyle(
    "KH1",
    parent=styles["Heading1"],
    fontName="MalgunBold",
    fontSize=14,
    leading=19,
    textColor=colors.HexColor("#1D4ED8"),
    spaceBefore=5,
    spaceAfter=7,
)
H2 = ParagraphStyle(
    "KH2",
    parent=styles["Heading2"],
    fontName="MalgunBold",
    fontSize=11.5,
    leading=16,
    textColor=colors.HexColor("#334155"),
    spaceBefore=5,
    spaceAfter=5,
)
BODY = ParagraphStyle(
    "KBody",
    parent=styles["BodyText"],
    fontName="Malgun",
    fontSize=8.7,
    leading=13.2,
    textColor=colors.HexColor("#172033"),
    spaceAfter=4,
    splitLongWords=True,
)
BULLET = ParagraphStyle(
    "KBullet",
    parent=BODY,
    leftIndent=10,
    firstLineIndent=-7,
    bulletIndent=2,
    spaceAfter=2.5,
)
CODE = ParagraphStyle(
    "KCode",
    parent=BODY,
    fontName="Malgun",
    fontSize=7.5,
    leading=11,
    leftIndent=7,
    rightIndent=7,
    borderColor=colors.HexColor("#CBD5E1"),
    borderWidth=0.5,
    borderPadding=6,
    backColor=colors.HexColor("#F8FAFC"),
    spaceBefore=3,
    spaceAfter=6,
)
TABLE_HEADER = ParagraphStyle(
    "KTableHeader",
    parent=BODY,
    fontName="MalgunBold",
    fontSize=7.4,
    leading=10,
    textColor=colors.white,
    alignment=TA_CENTER,
)
TABLE_CELL = ParagraphStyle(
    "KTableCell",
    parent=BODY,
    fontSize=7.2,
    leading=10.2,
    spaceAfter=0,
)
SMALL = ParagraphStyle(
    "KSmall",
    parent=BODY,
    fontSize=7.4,
    leading=10.5,
    textColor=colors.HexColor("#475569"),
)

PAGE_BREAK_PREFIXES = (
    "## 2.",
    "## 4.",
    "## 5.",
    "## 7.",
    "## 8.",
)


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<font name='MalgunBold' color='#1E3A8A'>\1</font>", escaped)
    return escaped


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def col_widths(headers: list[str], rows: list[list[str]]) -> list[float]:
    available = PAGE_W - 2 * MARGIN_X
    n = len(headers)
    if headers == ["우선순위", "Task", "완료 기준", "모델 재실행"]:
        return [available * 0.09, available * 0.20, available * 0.46, available * 0.25]
    if n == 4:
        return [available * 0.19, available * 0.16, available * 0.10, available * 0.55]
    if n == 5:
        return [
            available * 0.09,
            available * 0.20,
            available * 0.32,
            available * 0.23,
            available * 0.16,
        ]
    weights = []
    for index in range(n):
        longest = max(
            [len(headers[index])] + [len(row[index]) if index < len(row) else 0 for row in rows]
        )
        weights.append(min(max(longest, 8), 42))
    total = sum(weights)
    return [available * weight / total for weight in weights]


def make_table(headers: list[str], rows: list[list[str]]) -> LongTable:
    data = [[Paragraph(inline(cell), TABLE_HEADER) for cell in headers]]
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        data.append([Paragraph(inline(cell), TABLE_CELL) for cell in padded[: len(headers)]])
    table = LongTable(data, colWidths=col_widths(headers, rows), repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def parse_markdown(text: str) -> list:
    lines = text.splitlines()
    story: list = []
    paragraph: list[str] = []
    code_lines: list[str] = []
    in_code = False
    i = 0

    def flush_paragraph() -> None:
        if paragraph:
            story.append(Paragraph(inline(" ".join(part.strip() for part in paragraph)), BODY))
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush_paragraph()
            if in_code:
                story.append(Preformatted("\n".join(code_lines), CODE))
                code_lines.clear()
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        if (
            line.startswith("| ")
            and i + 1 < len(lines)
            and re.match(r"^\|?\s*:?-+", lines[i + 1].strip("| "))
        ):
            flush_paragraph()
            headers = split_table_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(split_table_row(lines[i]))
                i += 1
            story.extend([make_table(headers, rows), Spacer(1, 6)])
            continue
        if not line.strip():
            flush_paragraph()
            i += 1
            continue
        if line.startswith("# "):
            flush_paragraph()
            story.append(Paragraph(inline(line[2:]), TITLE))
            i += 1
            continue
        if line.startswith("## "):
            flush_paragraph()
            if any(line.startswith(prefix) for prefix in PAGE_BREAK_PREFIXES) and story:
                story.append(PageBreak())
            story.append(Paragraph(inline(line[3:]), H1))
            i += 1
            continue
        if line.startswith("### "):
            flush_paragraph()
            story.append(Paragraph(inline(line[4:]), H2))
            i += 1
            continue
        if re.match(r"^\d+\.\s", line):
            flush_paragraph()
            story.append(Paragraph(inline(line), BULLET))
            i += 1
            continue
        if line.startswith("- "):
            flush_paragraph()
            story.append(Paragraph(inline(line[2:]), BULLET, bulletText="•"))
            i += 1
            continue
        paragraph.append(line)
        i += 1
    flush_paragraph()
    return story


def draw_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.line(MARGIN_X, 11 * mm, PAGE_W - MARGIN_X, 11 * mm)
    canvas.setFont("Malgun", 7)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawString(MARGIN_X, 7 * mm, "4pms_paper · 위반 흐름 계측 피드백 코드 감사")
    canvas.drawRightString(PAGE_W - MARGIN_X, 7 * mm, f"{doc.page}")
    canvas.restoreState()


def main() -> None:
    doc = BaseDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title="위반 흐름 계측 피드백 코드 감사 보고서",
        author="Hermes Agent",
    )
    frame = Frame(
        MARGIN_X,
        MARGIN_BOTTOM,
        PAGE_W - 2 * MARGIN_X,
        PAGE_H - MARGIN_TOP - MARGIN_BOTTOM,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=draw_page))
    story = parse_markdown(SOURCE.read_text(encoding="utf-8"))
    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    main()
