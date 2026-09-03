from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


FONT_NAME = "SHARK-Label-A4"
FALLBACK_FONT_NAME = "HeiseiKakuGo-W5"


def _register_font():
    global FONT_NAME

    try:
        pdfmetrics.getFont(FONT_NAME)
        return
    except KeyError:
        pass

    base_dir = Path(__file__).resolve().parent.parent
    candidates = [
        base_dir / "fonts" / "NotoSansJP-VariableFont_wght.ttf",
        Path(r"C:\Windows\Fonts\meiryo.ttc"),
        Path(r"C:\Windows\Fonts\YuGothR.ttc"),
        Path(r"C:\Windows\Fonts\msgothic.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"),
    ]

    for font_path in candidates:
        if not font_path.exists():
            continue

        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(font_path)))
            return
        except Exception:
            continue

    FONT_NAME = FALLBACK_FONT_NAME

    try:
        pdfmetrics.getFont(FONT_NAME)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))


def _value(row, key, default=""):
    if isinstance(row, dict):
        value = row.get(key, default)
    else:
        try:
            value = row[key]
        except (KeyError, IndexError, TypeError):
            value = default

    return default if value is None else value


def _format_date(value):
    if isinstance(value, datetime):
        return value.strftime("%Y年%m月%d日")

    if isinstance(value, date):
        return value.strftime("%Y年%m月%d日")

    text = str(value or "").strip()
    if not text:
        return "-"

    normalized = text.replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(normalized).strftime("%Y年%m月%d日")
    except ValueError:
        return text


def _format_shipping_quantity(required_quantity, unit_label=""):
    """個別A4ラベルは通番、通常データは従来どおり出荷数を表示する。"""
    unit_label = str(unit_label or "").strip()
    if unit_label:
        return unit_label

    return f"{int(required_quantity or 0):,}"


def _fit_font_size(text, max_width, start_size, min_size=12):
    text = str(text or "")

    for font_size in range(start_size, min_size - 1, -1):
        if pdfmetrics.stringWidth(text, FONT_NAME, font_size) <= max_width:
            return font_size

    return min_size


def _truncate_text(text, max_width, font_size):
    text = str(text or "")

    if pdfmetrics.stringWidth(text, FONT_NAME, font_size) <= max_width:
        return text

    suffix = "…"
    shortened = text

    while shortened:
        shortened = shortened[:-1]
        candidate = shortened + suffix

        if pdfmetrics.stringWidth(
            candidate,
            FONT_NAME,
            font_size,
        ) <= max_width:
            return candidate

    return suffix


def _wrap_text(text, max_width, font_size, max_lines=2):
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

    return lines


def _draw_labeled_value(
    pdf,
    label,
    value,
    label_x,
    value_x,
    baseline_y,
    max_width,
    start_size,
):
    pdf.setFont(FONT_NAME, 13)
    pdf.drawString(label_x, baseline_y, label)

    value = str(value or "-")
    font_size = _fit_font_size(
        value,
        max_width,
        start_size,
        min_size=12,
    )
    value = _truncate_text(value, max_width, font_size)
    pdf.setFont(FONT_NAME, font_size)
    pdf.drawString(value_x, baseline_y - (1 * mm), value)


def _draw_qr_code(pdf, value, x, y, size):
    value = str(value or "").strip()
    if not value:
        return

    qr_code = createBarcodeDrawing(
        "QR",
        value=value,
        width=size,
        height=size,
        barBorder=4,
        barLevel="M",
    )

    pdf.saveState()
    pdf.translate(x, y)
    renderPDF.draw(qr_code, pdf, 0, 0)
    pdf.restoreState()


def _shark_base_create_shipping_a4_pdf_20260812(items):
    """選択商品をA4横向き・1商品1ページの出荷商品票にする。"""

    items = list(items)
    if not items:
        raise ValueError("A4に出力する商品がありません。")

    _register_font()

    buffer = BytesIO()
    page_size = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    page_width, page_height = page_size
    total_pages = len(items)

    for page_number, item in enumerate(items, start=1):
        company_name = str(
            _value(
                item,
                "company_name",
                _value(item, "customer_name", ""),
            )
        )
        project_name = str(_value(item, "project_name", ""))
        project_code = str(_value(item, "project_code", ""))
        item_name = str(_value(item, "item_name", ""))
        item_code = str(
            _value(
                item,
                "base_item_code",
                _value(item, "display_item_code", _value(item, "item_code", "")),
            )
        )
        qr_value = str(_value(item, "qr_value", _value(item, "item_code", "")))
        unit_label = str(_value(item, "unit_label", "") or "").strip()

        shipping_date = _format_date(
            _value(
                item,
                "shipping_date",
                _value(item, "ship_date", ""),
            )
        )
        required_quantity = int(_value(item, "required_quantity", 0) or 0)

        margin = 10 * mm
        inner_x = margin + (7 * mm)
        inner_right = page_width - margin - (7 * mm)
        inner_top = page_height - margin - (7 * mm)
        header_line_y = inner_top - (11 * mm)
        right_box_width = 72 * mm
        right_box_x = inner_right - right_box_width
        left_value_x = inner_x + (29 * mm)
        left_value_width = right_box_x - left_value_x - (8 * mm)

        # A4で印刷したときも枠と区切りがはっきり見える太さにする。
        pdf.setLineWidth(2.4)
        pdf.rect(
            margin,
            margin,
            page_width - (2 * margin),
            page_height - (2 * margin),
        )

        pdf.setFont(FONT_NAME, 19)
        pdf.drawString(inner_x, inner_top - (2 * mm), "出荷商品票")
        pdf.setFont(FONT_NAME, 12)
        pdf.drawRightString(
            inner_right,
            inner_top - (2 * mm),
            f"{page_number} / {total_pages}",
        )
        pdf.line(margin, header_line_y, page_width - margin, header_line_y)

        _draw_labeled_value(
            pdf,
            "荷主",
            company_name,
            inner_x,
            left_value_x,
            header_line_y - (17 * mm),
            left_value_width,
            26,
        )
        _draw_labeled_value(
            pdf,
            "案件",
            project_name,
            inner_x,
            left_value_x,
            header_line_y - (39 * mm),
            left_value_width,
            24,
        )
        _draw_labeled_value(
            pdf,
            "出荷日",
            shipping_date,
            inner_x,
            left_value_x,
            header_line_y - (61 * mm),
            left_value_width,
            22,
        )

        product_label_y = header_line_y - (82 * mm)
        pdf.setFont(FONT_NAME, 13)
        pdf.drawString(inner_x, product_label_y, "商品名")
        product_lines = _wrap_text(
            item_name,
            left_value_width,
            25,
            max_lines=3,
        )
        pdf.setFont(FONT_NAME, 25)

        for line_index, line in enumerate(product_lines):
            pdf.drawString(
                left_value_x,
                product_label_y + (2 * mm) - (line_index * 10 * mm),
                line,
            )

        quantity_box_y = margin + (8 * mm)
        quantity_box_top = header_line_y - (105 * mm)
        quantity_box_width = right_box_x - inner_x - (8 * mm)
        quantity_box_height = quantity_box_top - quantity_box_y
        pdf.rect(
            inner_x,
            quantity_box_y,
            quantity_box_width,
            quantity_box_height,
        )
        pdf.setFont(FONT_NAME, 14)
        pdf.drawString(
            inner_x + (7 * mm),
            quantity_box_top - (8 * mm),
            "出荷数",
        )
        quantity_text = _format_shipping_quantity(
            required_quantity,
            unit_label,
        )
        quantity_font_size = _fit_font_size(
            quantity_text,
            quantity_box_width - (40 * mm),
            50,
            min_size=30,
        )
        pdf.setFont(FONT_NAME, quantity_font_size)
        pdf.drawCentredString(
            inner_x + (quantity_box_width / 2),
            quantity_box_y + (16 * mm),
            quantity_text,
        )
        pdf.setFont(FONT_NAME, 17)
        pdf.drawRightString(
            inner_x + quantity_box_width - (9 * mm),
            quantity_box_y + (18 * mm),
            "個",
        )

        pdf.rect(
            right_box_x,
            margin + (8 * mm),
            right_box_width,
            header_line_y - margin - (8 * mm),
        )
        pdf.setFont(FONT_NAME, 13)
        pdf.drawString(
            right_box_x + (6 * mm),
            header_line_y - (14 * mm),
            "商品コード",
        )
        code_font_size = _fit_font_size(
            item_code,
            right_box_width - (12 * mm),
            22,
            min_size=10,
        )
        displayed_item_code = _truncate_text(
            item_code or "-",
            right_box_width - (12 * mm),
            code_font_size,
        )
        pdf.setFont(FONT_NAME, code_font_size)
        pdf.drawString(
            right_box_x + (6 * mm),
            header_line_y - (28 * mm),
            displayed_item_code,
        )

        if project_code:
            displayed_project_code = _truncate_text(
                f"案件コード：{project_code}",
                right_box_width - (12 * mm),
                10,
            )
            pdf.setFont(FONT_NAME, 10)
            pdf.drawString(
                right_box_x + (6 * mm),
                header_line_y - (40 * mm),
                displayed_project_code,
            )

        qr_size = 48 * mm
        _draw_qr_code(
            pdf,
            qr_value,
            right_box_x + ((right_box_width - qr_size) / 2),
            margin + (40 * mm),
            qr_size,
        )
        qr_caption = (
            f"{item_code}  {unit_label}"
            if unit_label
            else (item_code or "QRコードなし")
        )
        caption_font_size = _fit_font_size(
            qr_caption,
            right_box_width - (12 * mm),
            11,
            min_size=8,
        )
        displayed_caption = _truncate_text(
            qr_caption,
            right_box_width - (12 * mm),
            caption_font_size,
        )
        pdf.setFont(FONT_NAME, caption_font_size)
        pdf.drawCentredString(
            right_box_x + (right_box_width / 2),
            margin + (34 * mm),
            displayed_caption,
        )

        pdf.showPage()

    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()

# === SHARK A4 UNIT QR 20260812 ===
def _shark_unit_qr_parts(item):
    """出荷数分のA4用に、1/2・2/2形式の個別QR情報を作る。"""
    source = dict(item)

    base_code = str(
        source.get("base_item_code")
        or source.get("display_item_code")
        or source.get("item_code")
        or source.get("code")
        or ""
    ).strip()

    raw_qty = (
        source.get("required_quantity")
        if source.get("required_quantity") is not None
        else source.get("shipping_quantity")
        if source.get("shipping_quantity") is not None
        else source.get("quantity")
        if source.get("quantity") is not None
        else 1
    )

    try:
        total = int(raw_qty or 0)
    except (TypeError, ValueError):
        total = 1

    total = max(total, 0)
    return source, base_code, total


def _shark_expand_a4_unit_labels(items):
    """
    例:
      商品コード ABC / 出荷数2
        -> ABC|1/2
        -> ABC|2/2

    元のA4生成関数へ「出荷数分の行」を渡すだけなので、
    既存レイアウトはそのまま使う。
    """
    expanded = []

    for raw_item in list(items or []):
        source, base_code, total = _shark_unit_qr_parts(raw_item)

        if total <= 0:
            continue

        for sequence in range(1, total + 1):
            unit = dict(source)
            unit_label = f"{sequence}/{total}"

            unit_code = (
                f"{base_code}|{unit_label}"
                if base_code
                else unit_label
            )

            unit["base_item_code"] = base_code
            unit["display_item_code"] = base_code
            unit["unit_index"] = sequence
            unit["unit_total"] = total
            unit["unit_label"] = unit_label
            unit["unit_code"] = unit_code

            unit["qr_code"] = unit_code
            unit["qr_value"] = unit_code
            unit["barcode_value"] = unit_code

            # 商品コード表示は元コードのまま。QRだけ個別値を使う。
            expanded.append(unit)

    return expanded


def create_shipping_a4_pdf(*args, **kwargs):
    """SHARK A4完全版: 出荷数分に展開して個別QRを付与する。"""
    if args:
        expanded = _shark_expand_a4_unit_labels(args[0])
        return _shark_base_create_shipping_a4_pdf_20260812(expanded, *args[1:], **kwargs)

    for key in (
        "items",
        "selected_items",
        "rows",
        "labels",
        "label_items",
        "document_items",
    ):
        if key in kwargs:
            patched_kwargs = dict(kwargs)
            patched_kwargs[key] = _shark_expand_a4_unit_labels(
                patched_kwargs[key]
            )
            return _shark_base_create_shipping_a4_pdf_20260812(**patched_kwargs)

    raise TypeError(
        "create_shipping_a4_pdf: A4ラベル対象データを取得できませんでした。"
    )

# === SHARK A4 UNIT QR FINISH 20260812 ===
