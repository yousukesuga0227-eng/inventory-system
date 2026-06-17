import io
import os
import zipfile
from datetime import datetime

import streamlit as st

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from PIL import Image, ImageDraw, ImageFont

# Pillow 10以降で削除された Image.ANTIALIAS を brother_ql 用に復活
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

import barcode
from barcode.writer import ImageWriter

from brother_ql.raster import BrotherQLRaster
from brother_ql.conversion import convert
from brother_ql.backends.helpers import send

from database import get_connection


st.set_page_config(
    page_title="ラベル出力",
    page_icon="🏷️",
    layout="wide"
)

st.title("🏷️ ラベル出力")
st.caption("SHARK側で完成済みラベルPDF/PNGを作成し、QL-820へ直接印刷します。")


# =========================
# DB補助
# =========================
def is_postgres_connection(conn):
    """
    生のpsycopg接続ならTrue。
    CompatConnectionの場合は conn.execute 側で吸収する想定なのでFalse扱い。
    """
    module_name = conn.__class__.__module__.lower()
    class_name = conn.__class__.__name__.lower()

    if "compatconnection" in class_name:
        return False

    return "psycopg" in module_name


def placeholder(conn):
    """
    CompatConnectionは ? を内部で %s に変換してくれる想定。
    なので cursor を持たない場合は ? を使う。
    """
    if not hasattr(conn, "cursor"):
        return "?"

    return "%s" if is_postgres_connection(conn) else "?"


def row_to_dict(row, columns=None):
    if isinstance(row, dict):
        return row

    try:
        return dict(row)
    except Exception:
        if columns:
            return dict(zip(columns, row))

    return row


def fetch_all(conn, query, params=None):
    """
    sqlite / psycopg / CompatConnection 全対応の取得関数。
    """
    params = params or []

    # CompatConnection系：cursor() が無いので execute() を直接使う
    if not hasattr(conn, "cursor"):
        rows = conn.execute(query, params).fetchall()
        return [row_to_dict(row) for row in rows]

    # 通常のDB接続
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()

    columns = []
    if cur.description:
        columns = [desc[0] for desc in cur.description]

    result = [row_to_dict(row, columns) for row in rows]

    cur.close()
    return result


# =========================
# フォント
# =========================
import glob

def find_japanese_font():
    candidates = [
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\YuGothR.ttc",

        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf",

        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/truetype/takao-gothic/TakaoGothic.ttf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
    ]

    patterns = [
        "/usr/share/fonts/**/NotoSansCJK*.ttc",
        "/usr/share/fonts/**/NotoSansJP*.ttf",
        "/usr/share/fonts/**/*Gothic*.ttf",
        "/usr/share/fonts/**/*Gothic*.ttc",
        "/usr/share/fonts/**/*Mincho*.ttf",
        "/usr/share/fonts/**/*Mincho*.ttc",
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    for pattern in patterns:
        found = glob.glob(pattern, recursive=True)
        if found:
            return found[0]

    return None


FONT_PATH = find_japanese_font()

if FONT_PATH:
    st.sidebar.success(
        f"日本語フォントOK：{os.path.basename(FONT_PATH)}"
    )
else:
    st.sidebar.error(
        "日本語フォントが見つかりません。ラベル文字が化ける可能性があります。"
    )


def fit_text(draw, text, font_path, max_width, start_size, min_size=18):
    text = str(text or "")

    if not font_path:
        return ImageFont.load_default()

    size = start_size

    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]

        if width <= max_width:
            return font

        size -= 2

    return ImageFont.truetype(font_path, min_size)


def safe_filename(text):
    text = str(text or "")
    for ch in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        text = text.replace(ch, "_")
    return text.strip() or "item"


# =========================
# ラベル基本設定
# =========================
LABEL_WIDTH_MM = 62
LABEL_HEIGHT_MM = 38


# =========================
# QL-820 直接印刷 初期設定
# =========================
DEFAULT_QL820_IP = "192.168.0.18"
QL820_MODEL = "QL-820NWB"

# 今回成功したテストロール設定
DEFAULT_QL820_LABEL_SIZE = "62red"
DEFAULT_QL820_RED = True
DEFAULT_QL820_CUT = False


# =========================
# サイドバー設定
# =========================
st.sidebar.header("🖨️ QL-820 印刷設定")

ql820_ip = st.sidebar.text_input(
    "QL-820 IPアドレス",
    value=DEFAULT_QL820_IP
)

ql820_label_size = st.sidebar.selectbox(
    "ロール種類",
    options=[
        "62red",
        "62",
        "29x90",
        "29x62",
        "62x100",
    ],
    index=0
)

ql820_red = st.sidebar.checkbox(
    "赤黒ロールとして送信",
    value=DEFAULT_QL820_RED
)

ql820_cut = st.sidebar.checkbox(
    "印刷後にカット",
    value=DEFAULT_QL820_CUT
)

st.sidebar.caption(
    "今回のテストロールは 62red / 赤黒ON / カットOFF で成功。"
)


# =========================
# バーコード生成
# =========================
def make_barcode_png(code_text):
    """
    python-barcodeでCode128のPNGを作る。
    バーコード下の文字は表示しない。
    """
    code_text = str(code_text or "")

    buffer = io.BytesIO()

    code128_class = barcode.get_barcode_class("code128")
    barcode_obj = code128_class(code_text, writer=ImageWriter())

    barcode_obj.write(
        buffer,
        options={
            "module_width": 0.32,
            "module_height": 9,
            "font_size": 0,
            "text_distance": 0,
            "quiet_zone": 2,
            "write_text": False,
        }
    )

    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


# =========================
# ラベル画像生成
# =========================
def make_label_png(item):
    """
    1商品のラベルPNGを作成。
    QL-820 62mmロール想定。
    表示内容：
    上段：案件 / 出荷日
    中段：商品名
    下段：企業名
    最下段：バーコード
    """
    dpi = 300
    width_px = int(LABEL_WIDTH_MM / 25.4 * dpi)
    height_px = int(LABEL_HEIGHT_MM / 25.4 * dpi)

    img = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(img)

    font_path = FONT_PATH

    if font_path:
        font_header = ImageFont.truetype(font_path, 34)
        font_date = ImageFont.truetype(font_path, 22)
        font_item = ImageFont.truetype(font_path, 42)
        font_company = ImageFont.truetype(font_path, 24)
        font_small = ImageFont.truetype(font_path, 20)
    else:
        font_header = ImageFont.load_default()
        font_date = ImageFont.load_default()
        font_item = ImageFont.load_default()
        font_company = ImageFont.load_default()
        font_small = ImageFont.load_default()

    item_code = str(item.get("item_code", "") or "")
    item_name = str(item.get("item_name", "") or "")
    project_name = str(item.get("project_name", "") or "")

    # shipping_date がDBから取れてなければ空欄
    shipping_date = (
        item.get("shipping_date")
        or item.get("ship_date")
        or item.get("delivery_date")
        or ""
    )
    shipping_date = str(shipping_date or "")

    # 企業名が取れていない場合は、一旦案件名を企業名欄に出す
    company_name = (
        item.get("company_name")
        or item.get("customer_name")
        or project_name
        or ""
    )
    company_name = str(company_name or "")

    margin = 22
    right_margin = 22
    usable_width = width_px - margin - right_margin

    # 外枠
    draw.rectangle(
        [8, 8, width_px - 8, height_px - 8],
        outline="black",
        width=3
    )

    # =========================
    # 上段：案件 / 出荷日
    # =========================
    y = 16

    draw.text(
        (margin, y),
        "案件",
        fill="black",
        font=font_header
    )

    if shipping_date:
        date_text = f"出荷日：{shipping_date}"
    else:
        date_text = "出荷日：-"

    date_font = fit_text(
        draw,
        date_text,
        font_path,
        330,
        22,
        16
    )

    bbox = draw.textbbox((0, 0), date_text, font=date_font)
    date_w = bbox[2] - bbox[0]

    draw.text(
        (width_px - margin - date_w, y + 8),
        date_text,
        fill="black",
        font=date_font
    )

    # 区切り線
    y += 42
    draw.line(
        [(margin, y), (width_px - margin, y)],
        fill="black",
        width=2
    )

    # =========================
    # 商品名
    # =========================
    y += 14

    item_text = f"商品名：{item_name}"
    item_font = fit_text(
        draw,
        item_text,
        font_path,
        usable_width,
        42,
        26
    )

    draw.text(
        (margin, y),
        item_text,
        fill="black",
        font=item_font
    )

    # =========================
    # 企業名
    # =========================
    y += 50

    company_text = f"企業名：{company_name}"
    company_font = fit_text(
        draw,
        company_text,
        font_path,
        usable_width,
        24,
        18
    )

    draw.text(
        (margin, y),
        company_text,
        fill="black",
        font=company_font
    )

    # =========================
    # バーコード
    # =========================
    if item_code:
        barcode_img = make_barcode_png(item_code)

        # 幅を少し狭める
        target_w = width_px - 160
        ratio = target_w / barcode_img.width
        target_h = int(barcode_img.height * ratio)

        barcode_img = barcode_img.resize((target_w, target_h))

        x = int((width_px - target_w) / 2)
        y_bar = height_px - target_h - 22

        img.paste(barcode_img, (x, y_bar))

    return img


def make_label_pdf(items):
    """
    1商品 = 1ページのラベルPDFを作成。
    PNGラベルと同じ見た目をPDFに貼り付ける。
    """
    buffer = io.BytesIO()

    page_width = LABEL_WIDTH_MM * mm
    page_height = LABEL_HEIGHT_MM * mm

    c = canvas.Canvas(buffer, pagesize=(page_width, page_height))

    for item in items:
        img = make_label_png(item)

        img_buffer = io.BytesIO()
        img.save(img_buffer, format="PNG")
        img_buffer.seek(0)

        img_reader = ImageReader(img_buffer)

        c.drawImage(
            img_reader,
            0,
            0,
            width=page_width,
            height=page_height
        )

        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def make_png_zip(items):
    """
    選択商品のPNGをZIP化。
    """
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            item_code = str(item.get("item_code", "") or "no_code")
            item_name = str(item.get("item_name", "") or "item")

            img = make_label_png(item)

            png_buffer = io.BytesIO()
            img.save(png_buffer, format="PNG")
            png_buffer.seek(0)

            file_name = f"{safe_filename(item_code)}_{safe_filename(item_name)}.png"
            zf.writestr(file_name, png_buffer.getvalue())

    zip_buffer.seek(0)
    return zip_buffer.getvalue()


# =========================
# QL-820 直接印刷
# =========================
def print_labels_to_ql820(
    items,
    printer_ip,
    label_size,
    red,
    cut
):
    """
    選択商品のラベルPNGを生成して、QL-820へ直接印刷する。
    Windowsプリンタードライバーは使わず、ネットワーク経由で送信。
    """
    printed_count = 0

    for item in items:
        img = make_label_png(item).convert("RGB")

        qlr = BrotherQLRaster(QL820_MODEL)
        qlr.exception_on_warning = True

        instructions = convert(
            qlr=qlr,
            images=[img],
            label=label_size,
            rotate="auto",
            threshold=70.0,
            dither=False,
            compress=False,
            red=red,
            dpi_600=False,
            hq=True,
            cut=cut,
        )

        send(
            instructions=instructions,
            printer_identifier=f"tcp://{printer_ip}",
            backend_identifier="network",
            blocking=True,
        )

        printed_count += 1

    return printed_count


# =========================
# データ取得
# =========================
conn = get_connection()

try:
    projects = fetch_all(
        conn,
        """
        SELECT
            id,
            code,
            name
        FROM projects
        ORDER BY name
        """
    )

    if not projects:
        st.warning("案件が登録されていません。")
        st.stop()

    project_options = {
        f"{p.get('name', '')} / {p.get('code', '')}": p["id"]
        for p in projects
    }

    selected_project_label = st.selectbox(
        "案件を選択",
        list(project_options.keys())
    )

    selected_project_id = project_options[selected_project_label]

    ph = placeholder(conn)

    items = fetch_all(
        conn,
        f"""
        SELECT
            i.id AS item_id,
            i.code AS item_code,
            i.name AS item_name,

            p.code AS project_code,
            p.name AS project_name,
            p.shipping_date AS shipping_date,

            c.code AS company_code,
            c.name AS company_name

        FROM items i

        JOIN projects p
            ON i.project_id = p.id

        LEFT JOIN project_companies pc
            ON p.id = pc.project_id

        LEFT JOIN companies c
            ON pc.company_id = c.id

        WHERE i.project_id = {ph}
        ORDER BY i.name
        """,
        [selected_project_id]
    )

finally:
    try:
        conn.close()
    except Exception:
        pass


# =========================
# 画面表示
# =========================
if not items:
    st.info("この案件には商品が登録されていません。")
    st.stop()

st.subheader("商品一覧")

item_labels = {
    f"{item.get('item_name', '')} / {item.get('item_code', '')}": item
    for item in items
}

selected_item_labels = st.multiselect(
    "ラベル出力する商品を選択",
    list(item_labels.keys()),
    default=list(item_labels.keys())
)

selected_items = [
    item_labels[label]
    for label in selected_item_labels
]

st.write(f"選択中：{len(selected_items)} 件")

with st.expander("選択商品の確認", expanded=False):
    for item in selected_items:
        st.write(
            f"・{item.get('item_name', '')} / "
            f"{item.get('item_code', '')} / "
            f"{item.get('project_name', '')}"
        )


# =========================
# プレビュー
# =========================
with st.expander("ラベルプレビュー", expanded=False):
    if selected_items:
        preview_img = make_label_png(selected_items[0])
        st.image(
            preview_img,
            caption="選択中の先頭商品のラベルプレビュー",
            use_container_width=False
        )
    else:
        st.info("商品を選択するとプレビューを表示します。")


# =========================
# 出力ボタン
# =========================
if not selected_items:
    st.warning("商品を1件以上選択してください。")
    st.stop()

now = datetime.now().strftime("%Y%m%d_%H%M%S")

col1, col2, col3 = st.columns(3)

with col1:
    pdf_bytes = make_label_pdf(selected_items)

    st.download_button(
        label="📄 ラベルPDFをダウンロード",
        data=pdf_bytes,
        file_name=f"shark_labels_{now}.pdf",
        mime="application/pdf",
        use_container_width=True
    )

with col2:
    zip_bytes = make_png_zip(selected_items)

    st.download_button(
        label="🖼️ ラベルPNGをZIPでダウンロード",
        data=zip_bytes,
        file_name=f"shark_label_png_{now}.zip",
        mime="application/zip",
        use_container_width=True
    )

with col3:
    if st.button(
        "🖨️ QL-820へ直接印刷",
        use_container_width=True
    ):
        try:
            if not ql820_ip.strip():
                st.error("QL-820のIPアドレスを入力してください。")
                st.stop()

            with st.spinner("QL-820へ印刷データを送信中..."):
                printed_count = print_labels_to_ql820(
                    selected_items,
                    printer_ip=ql820_ip.strip(),
                    label_size=ql820_label_size,
                    red=ql820_red,
                    cut=ql820_cut
                )

            st.success(f"QL-820へ {printed_count} 件のラベルを送信しました。")

        except Exception as e:
            st.error("QL-820への直接印刷に失敗しました。")
            st.exception(e)


st.divider()

st.info(
    "まずは1件だけ選択して直接印刷を試してください。"
    "今回のテストロールは 62red / 赤黒ON / カットOFF で成功しています。"
)