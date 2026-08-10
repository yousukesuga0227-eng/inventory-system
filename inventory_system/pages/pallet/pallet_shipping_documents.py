"""
SHARK パレット出庫専用 納品書・受領書。
既存のPDF生成コードは変更せず、同じテイストで出力する。
"""

from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
FONT_PATH = ROOT_DIR / "fonts" / "NotoSansJP-VariableFont_wght.ttf"
LOGO_PATH = ROOT_DIR / "20260608-logo.png"
PDF_FONT = "Helvetica"

if FONT_PATH.exists():
    try:
        pdfmetrics.registerFont(
            TTFont("NotoSansJP", str(FONT_PATH))
        )
        PDF_FONT = "NotoSansJP"
    except Exception:
        PDF_FONT = "Helvetica"


def _value(row, key, default=""):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        try:
            value = row.get(key, default)
        except Exception:
            value = default
    return default if value is None else value


def _chunked(values, size):
    return [
        values[index:index + size]
        for index in range(0, len(values), size)
    ]


def _aggregate_shipments(shipments):
    grouped = defaultdict(
        lambda: {
            "category_name": "",
            "item_code": "",
            "item_name": "",
            "quantity": 0,
            "pallet_numbers": [],
        }
    )

    for row in shipments:
        category_name = str(
            _value(row, "category_name", "") or ""
        ).strip()
        item_code = str(
            _value(row, "item_code", "") or ""
        ).strip()
        item_name = str(
            _value(row, "item_name", "") or ""
        ).strip()

        key = (
            category_name,
            item_code,
            item_name,
        )

        grouped[key]["category_name"] = category_name
        grouped[key]["item_code"] = item_code
        grouped[key]["item_name"] = item_name
        grouped[key]["quantity"] += int(
            _value(
                row,
                "shipped_qty",
                _value(row, "quantity", 0),
            )
            or 0
        )

        sequence = int(
            _value(row, "category_sequence", 0)
            or 0
        )
        if sequence > 0:
            grouped[key]["pallet_numbers"].append(
                f"{sequence:03d}"
            )

    return list(grouped.values())


def create_pallet_shipment_document(
    title,
    shipments,
    issued_by="",
):
    shipments = list(shipments or [])

    if not shipments:
        raise ValueError(
            "帳票を作成する出庫データがありません。"
        )

    items = _aggregate_shipments(shipments)
    item_pages = _chunked(items, 10) or [[]]

    company_name = str(
        _value(shipments[0], "company_name", "") or ""
    ).strip()

    issued_at = datetime.now()
    pallet_count = len(shipments)
    total_qty = sum(
        int(
            _value(
                row,
                "shipped_qty",
                _value(row, "quantity", 0),
            )
            or 0
        )
        for row in shipments
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=7 * mm,
        leftMargin=7 * mm,
        topMargin=6 * mm,
        bottomMargin=8 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "SharkPalletTitle",
        parent=styles["Title"],
        fontName=PDF_FONT,
        fontSize=20,
        leading=22,
        alignment=1,
        textColor=colors.HexColor("#222222"),
        spaceAfter=0,
    )
    body_style = ParagraphStyle(
        "SharkPalletBody",
        parent=styles["BodyText"],
        fontName=PDF_FONT,
        fontSize=8,
        leading=9,
    )

    elements = []

    for page_index, page_items in enumerate(item_pages):
        if page_index > 0:
            elements.append(PageBreak())

        if LOGO_PATH.exists():
            logo = Image(str(LOGO_PATH))
            max_logo_width = 38 * mm
            max_logo_height = 10 * mm
            scale = min(
                max_logo_width / float(logo.imageWidth),
                max_logo_height / float(logo.imageHeight),
                1.0,
            )
            logo.drawWidth = float(logo.imageWidth) * scale
            logo.drawHeight = float(logo.imageHeight) * scale
            logo_cell = logo
        else:
            logo_cell = Paragraph("SHARK", body_style)

        document_title = Paragraph(
            f"【{escape(str(title))}】",
            title_style,
        )

        header = Table(
            [[logo_cell, document_title, ""]],
            colWidths=[55 * mm, 167 * mm, 55 * mm],
            rowHeights=[14 * mm],
        )
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    (
                        "LINEBELOW",
                        (0, 0),
                        (-1, -1),
                        0.7,
                        colors.HexColor("#666666"),
                    ),
                ]
            )
        )
        elements.append(header)
        elements.append(Spacer(1, 2 * mm))

        info_data = [
            [
                "荷主",
                company_name,
                "出庫日時",
                issued_at.strftime("%Y/%m/%d %H:%M"),
            ],
            [
                "発行者",
                str(issued_by or ""),
                "出庫パレット",
                f"{pallet_count:,}パレ",
            ],
            [
                "出庫数量",
                f"{total_qty:,}個",
                "ページ商品数",
                f"{len(page_items):,}品",
            ],
        ]

        info_table = Table(
            info_data,
            colWidths=[24 * mm, 112 * mm, 24 * mm, 117 * mm],
            rowHeights=[7 * mm] * 3,
        )
        info_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (0, -1),
                        colors.HexColor("#EAEAEA"),
                    ),
                    (
                        "BACKGROUND",
                        (2, 0),
                        (2, -1),
                        colors.HexColor("#EAEAEA"),
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(info_table)
        elements.append(Spacer(1, 2 * mm))

        table_data = [[
            "No.",
            "大カテゴリー",
            "商品コード",
            "商品名",
            "数量",
            "パレットNo.",
        ]]

        for index, item in enumerate(
            page_items,
            start=page_index * 10 + 1,
        ):
            table_data.append(
                [
                    str(index),
                    Paragraph(
                        escape(item["category_name"]),
                        body_style,
                    ),
                    item["item_code"],
                    Paragraph(
                        escape(item["item_name"]),
                        body_style,
                    ),
                    f"{int(item['quantity']):,}",
                    ", ".join(item["pallet_numbers"]),
                ]
            )

        item_table = Table(
            table_data,
            colWidths=[
                10 * mm,
                46 * mm,
                50 * mm,
                104 * mm,
                22 * mm,
                45 * mm,
            ],
            rowHeights=[8 * mm] + [11 * mm] * len(page_items),
            repeatRows=1,
        )
        item_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor("#EAEAEA"),
                    ),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (4, 1), (5, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        elements.append(item_table)
        elements.append(Spacer(1, 2 * mm))

        footer_table = Table(
            [
                ["備考", ""],
                [
                    "確認",
                    "担当：　　　　　　　　　"
                    "受領：　　　　　　　　　"
                    "日付：　　　　年　　月　　日",
                ],
            ],
            colWidths=[22 * mm, 255 * mm],
            rowHeights=[8 * mm, 8 * mm],
        )
        footer_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    (
                        "BACKGROUND",
                        (0, 0),
                        (0, -1),
                        colors.HexColor("#EAEAEA"),
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(footer_table)

    def add_page_number(pdf_canvas, current_doc):
        pdf_canvas.saveState()
        pdf_canvas.setFont(PDF_FONT, 8)
        pdf_canvas.drawRightString(
            landscape(A4)[0] - 8 * mm,
            5 * mm,
            f"{current_doc.page} / SHARK",
        )
        pdf_canvas.restoreState()

    doc.build(
        elements,
        onFirstPage=add_page_number,
        onLaterPages=add_page_number,
    )
    return buffer.getvalue()
