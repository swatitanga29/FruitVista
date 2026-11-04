"""Export rendering for the administrator Reports module.

Three formats are produced from the one report dictionary that
:mod:`services.reports_service` assembles, so a downloaded file always shows
the same figures as the page:

* CSV - built with pandas and written with a UTF-8 byte-order mark so the
  rupee sign opens correctly in Excel.
* XLSX - an overview sheet plus one sheet per report table, written through
  pandas with the openpyxl engine and lightly formatted.
* PDF - a laid-out report built with ReportLab, with the tables and a bar
  chart for each series.

Nothing here recalculates a figure or reads the database. The rupee sign is
spelled as ``Rs.`` in the PDF because the standard ReportLab fonts do not carry
that glyph; every other format uses the rupee sign.
"""

import io

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from services import reports_service

GREEN = colors.HexColor("#1b7f4b")
LIGHT_GREEN = colors.HexColor("#e8f3ec")
DARK = colors.HexColor("#1f2a24")
GREY = colors.HexColor("#5b6b61")

# How many charts the PDF draws, and how many labels each one keeps.
PDF_CHART_LIMIT = 3
PDF_CHART_LABELS = 12


# ---------------------------------------------------------------------------
# Shared layout helpers
# ---------------------------------------------------------------------------
def _grid_rows(report):
    """The whole report as a list of text rows, for the CSV export."""
    rows = [
        [report["app_name"]],
        [report["tagline"]],
        [report["title"]],
        ["Generated", report["generated_at"]],
        ["Generated (ISO)", report["generated_iso"]],
        [],
        ["Selected filters"],
    ]

    for item in report["filter_rows"]:
        rows.append([item["label"], item["value"]])

    rows.append([])
    rows.append(["Summary statistics"])
    for item in report["summary"]:
        rows.append([item["label"], item["value"]])

    rows.append([])

    for table in report["tables"]:
        rows.append([table["title"]])
        rows.append(list(table["columns"]))

        if table["rows"]:
            for row in table["rows"]:
                rows.append(list(row))
        else:
            rows.append(["No records match the selected filters."])

        if table.get("note"):
            rows.append([table["note"]])
        rows.append([])

    rows.append(["Notes"])
    for note in report["notes"]:
        rows.append([note])

    return rows


def _overview_rows(report):
    """The header, filters, summary and notes as label/value pairs."""
    rows = [
        ["Report", report["title"]],
        ["Application", report["app_name"]],
        ["Tagline", report["tagline"]],
        ["Generated", report["generated_at"]],
        [],
        ["Selected filters", ""],
    ]

    rows += [[item["label"], item["value"]] for item in report["filter_rows"]]
    rows.append([])
    rows.append(["Summary statistics", ""])
    rows += [[item["label"], item["value"]] for item in report["summary"]]
    rows.append([])
    rows.append(["Notes", ""])
    rows += [[note, ""] for note in report["notes"]]

    return rows


def _sheet_name(title, used):
    """Turn a table title into a unique, valid Excel sheet name."""
    cleaned = "".join(ch for ch in str(title) if ch not in ":\\/?*[]").strip()
    cleaned = (cleaned or "Table")[:31]

    name = cleaned
    counter = 2

    while name in used:
        suffix = f" {counter}"
        name = cleaned[: 31 - len(suffix)] + suffix
        counter += 1

    return name


def _layout_paragraphs(text, style):
    """Wrap a long note so it does not run off the page in the PDF."""
    return Paragraph(str(text).replace("&", "&amp;").replace("<", "&lt;"), style)


# ---------------------------------------------------------------------------
# CSV (pandas)
# ---------------------------------------------------------------------------
def csv_bytes(report):
    """Return the report as CSV bytes built with pandas."""
    rows = _grid_rows(report)
    width = max((len(row) for row in rows), default=0)
    padded = [row + [""] * (width - len(row)) for row in rows]

    frame = pd.DataFrame(padded)

    # The UTF-8 byte-order mark lets Excel read the rupee sign correctly.
    return frame.to_csv(index=False, header=False).encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Excel (pandas with the openpyxl engine)
# ---------------------------------------------------------------------------
def _style_workbook(workbook):
    """Bold the first row and size the columns to their content."""
    header_fill = PatternFill("solid", fgColor="E8F3EC")
    header_font = Font(bold=True, color="14603A")

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"

        for cell in sheet[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")

        for index, column in enumerate(sheet.iter_cols(), start=1):
            longest = 0
            for cell in column:
                value = "" if cell.value is None else str(cell.value)
                longest = max(longest, min(len(value), 60))

            letter = get_column_letter(index)
            sheet.column_dimensions[letter].width = max(14, min(52, longest + 4))


def xlsx_bytes(report):
    """Return the report as an Excel workbook built with openpyxl."""
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(_overview_rows(report), columns=["Item", "Value"]).to_excel(
            writer, sheet_name="Report", index=False
        )

        used = {"Report"}

        for table in report["tables"]:
            name = _sheet_name(table["title"], used)
            used.add(name)

            rows = table["rows"] or [["No records match the selected filters."]
                                     + [""] * (len(table["columns"]) - 1)]
            pd.DataFrame(rows, columns=table["columns"]).to_excel(
                writer, sheet_name=name, index=False
            )

        _style_workbook(writer.book)

    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF (ReportLab)
# ---------------------------------------------------------------------------
def _pdf_styles():
    """The paragraph styles used through the PDF report."""
    styles = getSampleStyleSheet()

    return {
        "brand": ParagraphStyle(
            "brand", parent=styles["Title"], fontName="Helvetica-Bold",
            fontSize=20, leading=24, textColor=GREEN, spaceAfter=2,
        ),
        "tagline": ParagraphStyle(
            "tagline", parent=styles["Normal"], fontName="Helvetica-Oblique",
            fontSize=10, leading=13, textColor=GREY, spaceAfter=10,
        ),
        "title": ParagraphStyle(
            "title", parent=styles["Heading1"], fontName="Helvetica-Bold",
            fontSize=15, leading=19, textColor=DARK, spaceBefore=4, spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "meta", parent=styles["Normal"], fontName="Helvetica",
            fontSize=9, leading=12, textColor=GREY, spaceAfter=10,
        ),
        "section": ParagraphStyle(
            "section", parent=styles["Heading2"], fontName="Helvetica-Bold",
            fontSize=11.5, leading=15, textColor=GREEN, spaceBefore=12, spaceAfter=5,
        ),
        "note": ParagraphStyle(
            "note", parent=styles["Normal"], fontName="Helvetica-Oblique",
            fontSize=8, leading=11, textColor=GREY, spaceBefore=3, spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "body", parent=styles["Normal"], fontName="Helvetica",
            fontSize=9, leading=12, textColor=DARK, spaceAfter=3,
        ),
    }


def _column_widths(columns, rows, total_width):
    """Size the columns to their longest value, never below a sensible minimum."""
    weights = []

    for index, column in enumerate(columns):
        longest = len(str(column))
        for row in rows:
            if index < len(row):
                longest = max(longest, len(str(row[index])))
        weights.append(min(max(longest, 8), 40))

    combined = sum(weights) or 1

    return [total_width * weight / combined for weight in weights]


def _pdf_table(table, styles):
    """One report table as a ReportLab flowable."""
    columns = [str(column) for column in table["columns"]]
    rows = [[str(cell) for cell in row] for row in table["rows"]]

    if not rows:
        return [_layout_paragraphs("No records match the selected filters.", styles["note"])]

    data = [columns] + rows
    widths = _column_widths(columns, rows, 500)

    flowable = Table(data, colWidths=widths, repeatRows=1)
    flowable.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_GREEN),
        ("TEXTCOLOR", (0, 0), (-1, 0), GREEN),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("LEADING", (0, 1), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfdcd3")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf8")]),
    ]))

    return [flowable]


def _pdf_chart(chart):
    """A simple bar chart drawing for one report series, or ``None``."""
    labels = [str(label) for label in chart["labels"]]
    values = [float(value or 0) for value in chart["values"]]

    if not labels or not values or len(labels) != len(values):
        return None

    if len(labels) > PDF_CHART_LABELS:
        # Keep the most useful end of the series: the newest months for a time
        # axis, or the largest entries when the series is already ranked.
        if chart.get("horizontal"):
            labels, values = labels[:PDF_CHART_LABELS], values[:PDF_CHART_LABELS]
        else:
            labels, values = labels[-PDF_CHART_LABELS:], values[-PDF_CHART_LABELS:]

    short = [(label[:14] + "\u2026") if len(label) > 15 else label for label in labels]

    try:
        if chart.get("horizontal"):
            height = 24 + 15 * len(short)
            drawing = Drawing(500, height)
            bar = HorizontalBarChart()
            bar.width = 380
            bar.height = height - 18
            bar.x = 110
            bar.y = 8
            bar.data = [values]
            bar.categoryAxis.categoryNames = short
            bar.categoryAxis.labels.fontSize = 7
            bar.valueAxis.valueMin = 0
            bar.bars[0].fillColor = GREEN
        else:
            drawing = Drawing(500, 190)
            bar = VerticalBarChart()
            bar.width = 470
            bar.height = 150
            bar.x = 18
            bar.y = 30
            bar.data = [values]
            bar.categoryAxis.categoryNames = short
            bar.categoryAxis.labels.fontSize = 7
            bar.categoryAxis.labels.angle = 20
            bar.categoryAxis.labels.dy = -6
            bar.valueAxis.valueMin = 0
            bar.bars[0].fillColor = GREEN

        drawing.add(bar)
        return drawing
    except Exception:  # pragma: no cover - a chart must never break the report
        return None


def _pdf_footer(report):
    """Draw the footer with the tagline and the page number on every page."""
    tagline = report["tagline"]

    def draw(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#cfdcd3"))
        canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(18 * mm, 11 * mm, f"{report['app_name']} \u2013 {tagline}")
        canvas.drawRightString(
            A4[0] - 18 * mm, 11 * mm, f"Generated {report['generated_at']} | Page {document.page}"
        )
        canvas.restoreState()

    return draw


def _currency_text(value, symbol=reports_service.PDF_CURRENCY):
    """Reformat a rupee value with the glyph-safe ``Rs.`` prefix."""
    if value.startswith(reports_service.ISO_CURRENCY):
        return symbol + value[len(reports_service.ISO_CURRENCY):]
    return value


def pdf_bytes(report):
    """Return the report as a PDF built with ReportLab."""
    styles = _pdf_styles()
    buffer = io.BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"{report['app_name']} {report['title']}",
        author=report["app_name"],
    )

    story = [
        Paragraph(report["app_name"], styles["brand"]),
        Paragraph(report["tagline"], styles["tagline"]),
        Paragraph(report["title"], styles["title"]),
        Paragraph(f"Generated on {report['generated_at']}", styles["meta"]),
    ]

    story.append(Paragraph("Selected filters", styles["section"]))
    filter_data = [[item["label"], item["value"]] for item in report["filter_rows"]]
    filters = Table(filter_data, colWidths=[120, 380])
    filters.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), DARK),
        ("TEXTCOLOR", (1, 0), (1, -1), GREY),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#eef4f0")),
    ]))
    story.append(filters)

    story.append(Paragraph("Summary statistics", styles["section"]))
    summary_data = [[item["label"], _currency_text(item["value"])]
                    for item in report["summary"]]
    summary = Table(summary_data, colWidths=[300, 200])
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7faf8")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dfeae3")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(summary)

    chart_blocks = []
    for chart in report["charts"][:PDF_CHART_LIMIT]:
        drawing = _pdf_chart(chart)
        if drawing is not None:
            chart_blocks.append((chart["title"], drawing))

    if chart_blocks:
        story.append(Paragraph("Charts", styles["section"]))

        for title, drawing in chart_blocks:
            story.append(KeepTogether([
                Paragraph(title, styles["body"]),
                drawing,
                Spacer(1, 6),
            ]))

    for table in report["tables"]:
        block = [Paragraph(table["title"], styles["section"])]
        block += _pdf_table(table, styles)

        if table.get("note"):
            block.append(_layout_paragraphs(table["note"], styles["note"]))

        story.append(KeepTogether(block))

    story.append(Paragraph("Notes", styles["section"]))
    for note in report["notes"]:
        story.append(_layout_paragraphs(note, styles["body"]))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Prepared by {report['app_name']} \u2013 {report['tagline']}. "
        "All amounts are in Indian Rupees (INR).",
        styles["note"],
    ))

    document.build(story, onFirstPage=_pdf_footer(report), onLaterPages=_pdf_footer(report))

    return buffer.getvalue()
