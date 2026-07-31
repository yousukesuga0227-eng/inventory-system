"""パレット用A4票のPDF作成。"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


FONT_NAME = "SHARK-NotoSansJP"
FALLBACK_FONT_NAME = "HeiseiKakuGo-W5"
JST = timezone(timedelta(hours=9), name="JST")


def _register_font():
    global FONT_NAME

    try:
        pdfmetrics.getFont(FONT_NAME)
        return
    except KeyError:
        pass

    base_dir = Path(__file__).resolve().parent
    font_candidates = [
        base_dir / "assets" / "NotoSansJP-Regular.ttf",
        Path(r"C:\Windows\Fonts\meiryo.ttc"),
        Path(r"C:\Windows\Fonts\YuGothR.ttc"),
        Path(r"C:\Windows\Fonts\msgothic.ttc"),
        Path(
            "/usr/share/fonts/opentype/noto/"
            "NotoSansCJK-Regular.ttc"
        ),
        Path(
            "/usr/share/fonts/truetype/noto/"
            "NotoSansJP-Regular.ttf"
        ),
    ]

    for font_path in font_candidates:
        if not font_path.exists():
            continue

        try:
            pdfmetrics.registerFont(
                TTFont(FONT_NAME, str(font_path))
            )
            return
        except Exception:
            continue

    FONT_NAME = FALLBACK_FONT_NAME

    try:
        pdfmetrics.getFont(FONT_NAME)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))


def _value(row, key, default=""):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default

    return default if value is None else value


def _format_date(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(JST)
        return value.strftime("%Y年%m月%d日")

    text = str(value or "").strip()

    if not text:
        return ""

    normalized = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日")
    except ValueError:
        return text[:10].replace("-", "年", 1).replace("-", "月", 1) + "日"


def _draw_centered_wrapped_text(
    pdf,
    text,
    center_x,
    top_y,
    max_width,
    font_size,
    line_height,
    max_lines=2,
):
    text = str(text or "")
    lines = []
    current = ""

    for character in text:
        candidate = current + character

        if (
            current
            and pdfmetrics.stringWidth(
                candidate,
                FONT_NAME,
                font_size,
            ) > max_width
        ):
            lines.append(current)
            current = character
        else:
            current = candidate

    if current:
        lines.append(current)

    if not lines:
        lines = [""]

    lines = lines[:max_lines]

    if len(lines) == max_lines:
        original_width = pdfmetrics.stringWidth(
            lines[-1],
            FONT_NAME,
            font_size,
        )
        if original_width > max_width:
            while (
                lines[-1]
                and pdfmetrics.stringWidth(
                    lines[-1] + "…",
                    FONT_NAME,
                    font_size,
                ) > max_width
            ):
                lines[-1] = lines[-1][:-1]
            lines[-1] += "…"

    for index, line in enumerate(lines):
        pdf.drawCentredString(
            center_x,
            top_y - (index * line_height),
            line,
        )


def _draw_qr(pdf, value, x, y, size):
    widget = qr.QrCodeWidget(value)
    x1, y1, x2, y2 = widget.getBounds()
    width = x2 - x1
    height = y2 - y1

    drawing = Drawing(
        size,
        size,
        transform=[
            size / width,
            0,
            0,
            size / height,
            0,
            0,
        ],
    )
    drawing.add(widget)
    renderPDF.draw(drawing, pdf, x, y)


def create_pallet_a4_pdf(pallets):
    """
    1パレットにつきA4を1ページ作る。

    A4へ載せる内容：
    パレット番号、荷主、商品名、数量、入庫日、QRコード。
    """

    pallets = list(pallets)

    if not pallets:
        raise ValueError("A4票を作成するパレットがありません。")

    _register_font()
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4

    for pallet in pallets:
        sequence = int(_value(pallet, "pallet_sequence", 1))
        total_pallets = int(_value(pallet, "total_pallets", 1))
        pallet_number = f"{sequence:03d} / {total_pallets:03d}"
        pallet_code = str(_value(pallet, "pallet_code", ""))
        company_name = str(_value(pallet, "company_name", ""))
        item_name = str(_value(pallet, "item_name", ""))
        quantity = int(
            _value(
                pallet,
                "current_qty",
                _value(pallet, "initial_qty", 0),
            )
        )
        received_date = _format_date(
            _value(pallet, "created_at", "")
        )

        margin_x = 15 * mm
        content_width = page_width - (margin_x * 2)

        pdf.setLineWidth(1.2)
        pdf.rect(
            margin_x,
            15 * mm,
            content_width,
            page_height - (30 * mm),
        )

        pdf.setFont(FONT_NAME, 20)
        pdf.drawCentredString(
            page_width / 2,
            page_height - (25 * mm),
            "パレット票",
        )

        pdf.setFont(FONT_NAME, 56)
        pdf.drawCentredString(
            page_width / 2,
            page_height - (50 * mm),
            pallet_number,
        )

        top = page_height - (64 * mm)
        row_height = 30 * mm
        label_width = 34 * mm

        for row_index in range(3):
            row_y = top - ((row_index + 1) * row_height)
            pdf.line(
                margin_x,
                row_y,
                margin_x + content_width,
                row_y,
            )

        pdf.line(
            margin_x + label_width,
            top,
            margin_x + label_width,
            top - (row_height * 3),
        )

        labels = ["荷主", "商品名", "数量"]
        values = [
            company_name,
            item_name,
            f"{quantity:,} 個",
        ]

        for row_index, (label, value) in enumerate(
            zip(labels, values)
        ):
            row_top = top - (row_index * row_height)
            center_y = row_top - (row_height / 2)

            pdf.setFont(FONT_NAME, 16)
            pdf.drawCentredString(
                margin_x + (label_width / 2),
                center_y - (5 * mm),
                label,
            )

            value_center_x = (
                margin_x
                + label_width
                + ((content_width - label_width) / 2)
            )

            value_font_size = 30 if label != "商品名" else 20
            pdf.setFont(FONT_NAME, value_font_size)

            _draw_centered_wrapped_text(
                pdf=pdf,
                text=value,
                center_x=value_center_x,
                top_y=center_y + (2 * mm),
                max_width=content_width - label_width - (10 * mm),
                font_size=value_font_size,
                line_height=value_font_size + 3,
                max_lines=2,
            )

        lower_top = top - (row_height * 3)

        pdf.setFont(FONT_NAME, 16)
        pdf.drawString(
            margin_x + (6 * mm),
            lower_top - (14 * mm),
            f"入庫日：{received_date}",
        )

        qr_size = 62 * mm
        qr_x = (page_width - qr_size) / 2
        qr_y = 28 * mm

        _draw_qr(
            pdf=pdf,
            value=pallet_code,
            x=qr_x,
            y=qr_y,
            size=qr_size,
        )

        pdf.setFont(FONT_NAME, 12)
        pdf.drawCentredString(
            page_width / 2,
            22 * mm,
            pallet_code,
        )

        pdf.showPage()

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()
