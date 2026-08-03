"""パレット用A4票のPDF作成。"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4, landscape
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

    was_truncated = len(lines) > max_lines
    lines = lines[:max_lines]

    if was_truncated:
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
    1パレットにつきA4横向きを1ページ作る。

    A4へ載せる内容：
    パレット番号、荷主、大カテゴリー、商品名、数量、入庫日、QRコード。
    """

    pallets = list(pallets)

    if not pallets:
        raise ValueError("A4票を作成するパレットがありません。")

    _register_font()
    buffer = BytesIO()
    page_size = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    page_width, page_height = page_size

    for pallet in pallets:
        category_sequence = int(
            _value(
                pallet,
                "category_sequence",
                _value(pallet, "pallet_sequence", 1),
            )
        )
        pallet_number = f"{category_sequence:03d}"
        pallet_code = str(_value(pallet, "pallet_code", ""))
        company_name = str(_value(pallet, "company_name", ""))
        category_name = str(_value(pallet, "category_name", ""))
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

        margin = 10 * mm
        inner_x = margin + (7 * mm)
        inner_top = page_height - margin - (7 * mm)
        content_width = page_width - (2 * margin)
        qr_size = 61 * mm
        qr_x = page_width - margin - qr_size - (7 * mm)
        qr_y = 22 * mm
        left_width = qr_x - inner_x - (8 * mm)

        pdf.setLineWidth(1.4)
        pdf.rect(
            margin,
            margin,
            content_width,
            page_height - (2 * margin),
        )

        pdf.setFont(FONT_NAME, 19)
        pdf.drawString(
            inner_x,
            inner_top - (2 * mm),
            "パレット票",
        )
        pdf.setFont(FONT_NAME, 25)
        pdf.drawRightString(
            page_width - margin - (7 * mm),
            inner_top - (2 * mm),
            pallet_number,
        )

        header_line_y = inner_top - (10 * mm)
        pdf.line(
            margin,
            header_line_y,
            page_width - margin,
            header_line_y,
        )

        label_x = inner_x
        value_x = inner_x + (32 * mm)
        value_width = left_width - (32 * mm)

        rows = [
            ("入庫日", received_date, 26, header_line_y - (16 * mm)),
            ("荷主", company_name, 27, header_line_y - (36 * mm)),
            (
                "大カテゴリー",
                category_name,
                21,
                header_line_y - (56 * mm),
            ),
            ("商品名", item_name, 25, header_line_y - (74 * mm)),
        ]

        for label, value, font_size, baseline_y in rows:
            pdf.setFont(FONT_NAME, 14)
            pdf.drawString(label_x, baseline_y, label)
            pdf.setFont(FONT_NAME, font_size)
            _draw_centered_wrapped_text(
                pdf=pdf,
                text=value,
                center_x=value_x + (value_width / 2),
                top_y=baseline_y + (3 * mm),
                max_width=value_width,
                font_size=font_size,
                line_height=font_size + 4,
                max_lines=2,
            )

        quantity_box_top = header_line_y - (94 * mm)
        quantity_box_bottom = margin + (12 * mm)
        pdf.rect(
            inner_x,
            quantity_box_bottom,
            left_width,
            quantity_box_top - quantity_box_bottom,
        )
        pdf.setFont(FONT_NAME, 14)
        pdf.drawString(
            inner_x + (7 * mm),
            quantity_box_top - (11 * mm),
            "数量",
        )
        # 倉庫で離れて見ても数量を確認できる大きさを維持する。
        pdf.setFont(FONT_NAME, 50)
        pdf.drawCentredString(
            inner_x + (left_width / 2),
            quantity_box_bottom + (8 * mm),
            f"{quantity:,} 個",
        )

        pdf.setFont(FONT_NAME, 16)
        pdf.drawCentredString(
            qr_x + (qr_size / 2),
            header_line_y - (20 * mm),
            pallet_number,
        )

        _draw_qr(
            pdf=pdf,
            value=pallet_code,
            x=qr_x,
            y=qr_y,
            size=qr_size,
        )

        pdf.setFont(FONT_NAME, 12)
        pdf.drawCentredString(
            qr_x + (qr_size / 2),
            16 * mm,
            pallet_code,
        )

        pdf.showPage()

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def create_receiving_plan_a4_pdf(plans):
    """
    入庫予定の管理票をA4横向きで作る。

    1パレットにつき1ページを作成し、事前に印刷したQRを
    入庫確定後もそのままQR出庫で使用できるようにする。
    """

    plans = list(plans)

    if not plans:
        raise ValueError("A4管理票を作成する入庫予定がありません。")

    _register_font()
    buffer = BytesIO()
    page_size = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    page_width, page_height = page_size

    for plan in plans:
        receipt_code = str(_value(plan, "receipt_code", ""))
        receiving_date = _format_date(
            _value(plan, "receiving_date", "")
        )
        company_name = str(_value(plan, "company_name", ""))
        category_name = str(_value(plan, "category_name", ""))
        item_name = str(_value(plan, "item_name", ""))
        pallet_count = int(_value(plan, "pallet_count", 1))
        category_start_sequence = int(
            _value(plan, "category_start_sequence", 1)
        )
        qty_per_pallet = int(_value(plan, "qty_per_pallet", 0))
        total_qty = int(
            _value(
                plan,
                "total_qty",
                pallet_count * qty_per_pallet,
            )
        )

        for sequence in range(1, pallet_count + 1):
            pallet_code = f"{receipt_code}-P{sequence:03d}"
            pallet_number = (
                category_start_sequence + sequence - 1
            )
            margin = 10 * mm
            inner_x = margin + (7 * mm)
            inner_top = page_height - margin - (7 * mm)
            content_width = page_width - (2 * margin)
            qr_size = 61 * mm
            qr_x = page_width - margin - qr_size - (7 * mm)
            qr_y = 22 * mm
            left_width = qr_x - inner_x - (8 * mm)

            pdf.setLineWidth(1.4)
            pdf.rect(
                margin,
                margin,
                content_width,
                page_height - (2 * margin),
            )

            pdf.setFont(FONT_NAME, 19)
            pdf.drawString(inner_x, inner_top - (2 * mm), "入庫管理票")
            pdf.setFont(FONT_NAME, 20)
            pdf.drawRightString(
                page_width - margin - (7 * mm),
                inner_top - (2 * mm),
                receipt_code,
            )
            header_line_y = inner_top - (10 * mm)
            pdf.line(
                margin,
                header_line_y,
                page_width - margin,
                header_line_y,
            )

            label_x = inner_x
            value_x = inner_x + (38 * mm)
            value_width = left_width - (38 * mm)

            rows = [
                ("入庫日", receiving_date, 28, header_line_y - (16 * mm)),
                ("顧客", company_name, 28, header_line_y - (36 * mm)),
                (
                    "大カテゴリー",
                    category_name,
                    21,
                    header_line_y - (55 * mm),
                ),
                ("商品名", item_name, 25, header_line_y - (73 * mm)),
            ]

            for label, value, font_size, baseline_y in rows:
                pdf.setFont(FONT_NAME, 14)
                pdf.drawString(label_x, baseline_y, label)
                pdf.setFont(FONT_NAME, font_size)
                _draw_centered_wrapped_text(
                    pdf=pdf,
                    text=value,
                    center_x=value_x + (value_width / 2),
                    top_y=baseline_y + (3 * mm),
                    max_width=value_width,
                    font_size=font_size,
                    line_height=font_size + 4,
                    max_lines=2,
                )

            box_top = header_line_y - (92 * mm)
            box_bottom = margin + (12 * mm)
            box_width = left_width / 3
            numeric_values = [
                ("パレット枚数", f"{pallet_count:,} 枚"),
                ("1パレットの商品数", f"{qty_per_pallet:,} 個"),
                ("商品総数", f"{total_qty:,} 個"),
            ]

            for index, (label, value) in enumerate(numeric_values):
                box_x = inner_x + (box_width * index)
                pdf.rect(
                    box_x,
                    box_bottom,
                    box_width,
                    box_top - box_bottom,
                )
                pdf.setFont(FONT_NAME, 12)
                pdf.drawCentredString(
                    box_x + (box_width / 2),
                    box_top - (10 * mm),
                    label,
                )
                pdf.setFont(FONT_NAME, 34)
                pdf.drawCentredString(
                    box_x + (box_width / 2),
                    box_bottom + (10 * mm),
                    value,
                )

            pdf.setFont(FONT_NAME, 18)
            pdf.drawCentredString(
                qr_x + (qr_size / 2),
                header_line_y - (20 * mm),
                f"{pallet_number:03d}",
            )
            _draw_qr(
                pdf=pdf,
                value=pallet_code,
                x=qr_x,
                y=qr_y,
                size=qr_size,
            )
            pdf.setFont(FONT_NAME, 11)
            pdf.drawCentredString(
                qr_x + (qr_size / 2),
                16 * mm,
                pallet_code,
            )
            pdf.showPage()

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()
