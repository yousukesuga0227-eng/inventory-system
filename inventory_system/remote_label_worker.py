import io
import os
import signal
import time
from datetime import datetime

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
from barcode_serials import split_unit_barcode


# =========================================================
# 設定
# =========================================================

# 倉庫へ持って行った時、QL-820の実際のIPに変更する
PRINTER_IP = os.environ.get(
    "SHARK_PRINTER_IP",
    "192.168.0.4"
).strip()

PRINTER_MODEL = "QL-820NWB"

# DK-2205：62mm幅の白テープ
LABEL_SIZE = "62"

RED_PRINT = False
CUT_LABEL = True

# Supabaseを確認する間隔
POLL_SECONDS = 3

LABEL_WIDTH_MM = 62
LABEL_HEIGHT_MM = 38

RUNNING = True


# =========================================================
# 終了処理
# =========================================================
def stop_worker(signum=None, frame=None):
    global RUNNING

    RUNNING = False
    print()
    print("🛑 遠隔印刷ワーカーを終了します...")


signal.signal(signal.SIGINT, stop_worker)

if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, stop_worker)


# =========================================================
# DB補助
# =========================================================
def is_postgres_connection(conn):
    module_name = conn.__class__.__module__.lower()
    class_name = conn.__class__.__name__.lower()

    if "compatconnection" in class_name:
        return False

    return "psycopg" in module_name


def convert_query_for_connection(conn, query):
    """
    CompatConnectionは ? を内部変換するのでそのまま。
    生のpsycopg接続だけ ? を %s に変換する。
    """
    if is_postgres_connection(conn):
        return query.replace("?", "%s")

    return query


def row_to_dict(row, columns=None):
    if row is None:
        return None

    if isinstance(row, dict):
        return row

    # database.py の RowLike対応
    if hasattr(row, "keys"):
        return {
            key: row[key]
            for key in row.keys()
        }

    if columns:
        return dict(
            zip(columns, row)
        )

    raise TypeError(
        f"辞書へ変換できない行形式です: {type(row)}"
    )


def fetch_one(conn, query, params=None):
    params = params or []
    query = convert_query_for_connection(conn, query)

    # CompatConnection / SQLiteラッパー
    if not hasattr(conn, "cursor"):
        row = conn.execute(query, params).fetchone()
        return row_to_dict(row)

    # 生のsqlite / psycopg
    cur = conn.cursor()

    try:
        cur.execute(query, params)
        row = cur.fetchone()

        columns = []

        if cur.description:
            columns = [
                desc[0]
                for desc in cur.description
            ]

        return row_to_dict(row, columns)

    finally:
        cur.close()


def execute_write(conn, query, params=None):
    params = params or []
    query = convert_query_for_connection(conn, query)

    if not hasattr(conn, "cursor"):
        return conn.execute(query, params)

    cur = conn.cursor()

    try:
        cur.execute(query, params)
        return cur.rowcount

    finally:
        cur.close()


# =========================================================
# 日本語フォント
# =========================================================
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
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


FONT_PATH = find_japanese_font()


def fit_text(
    draw,
    text,
    font_path,
    max_width,
    start_size,
    min_size=18
):
    text = str(text or "")

    if not font_path:
        return ImageFont.load_default()

    size = start_size

    while size >= min_size:
        font = ImageFont.truetype(
            font_path,
            size
        )

        bbox = draw.textbbox(
            (0, 0),
            text,
            font=font
        )

        width = bbox[2] - bbox[0]

        if width <= max_width:
            return font

        size -= 2

    return ImageFont.truetype(
        font_path,
        min_size
    )


# =========================================================
# バーコード生成
# =========================================================
def make_barcode_png(code_text):
    code_text = str(code_text or "")

    buffer = io.BytesIO()

    code128_class = barcode.get_barcode_class(
        "code128"
    )

    barcode_obj = code128_class(
        code_text,
        writer=ImageWriter()
    )

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


# =========================================================
# ラベル画像生成
# =========================================================
def make_label_png(job):
    dpi = 300

    width_px = int(
        LABEL_WIDTH_MM / 25.4 * dpi
    )

    height_px = int(
        LABEL_HEIGHT_MM / 25.4 * dpi
    )

    img = Image.new(
        "RGB",
        (width_px, height_px),
        "white"
    )

    draw = ImageDraw.Draw(img)

    item_code = str(
        job.get("item_code", "") or ""
    )
    _, unit_number = split_unit_barcode(item_code)

    item_name = str(
        job.get("item_name", "") or ""
    )

    project_name = str(
        job.get("project_name", "") or ""
    )

    shipping_date = str(
        job.get("shipping_date", "") or ""
    )

    company_name = str(
        job.get("company_name", "")
        or project_name
        or ""
    )

    margin = 22
    right_margin = 22

    usable_width = (
        width_px
        - margin
        - right_margin
    )

    # 外枠
    draw.rectangle(
        [
            8,
            8,
            width_px - 8,
            height_px - 8
        ],
        outline="black",
        width=3
    )

    # =========================
    # 上段：案件名・出荷日
    # =========================
    y = 16

    project_text = f"案件：{project_name}"

    project_font = fit_text(
        draw,
        project_text,
        FONT_PATH,
        520,
        28,
        18
    )

    draw.text(
        (margin, y),
        project_text,
        fill="black",
        font=project_font
    )

    if shipping_date:
        date_text = f"出荷日：{shipping_date}"
    else:
        date_text = "出荷日：-"

    date_font = fit_text(
        draw,
        date_text,
        FONT_PATH,
        300,
        22,
        16
    )

    bbox = draw.textbbox(
        (0, 0),
        date_text,
        font=date_font
    )

    date_width = bbox[2] - bbox[0]

    draw.text(
        (
            width_px
            - margin
            - date_width,
            y + 8
        ),
        date_text,
        fill="black",
        font=date_font
    )

    # 区切り線
    y += 42

    draw.line(
        [
            (margin, y),
            (width_px - margin, y)
        ],
        fill="black",
        width=2
    )

    # =========================
    # 商品名
    # =========================
    y += 12

    item_text = f"商品名：{item_name}"

    if FONT_PATH:
        item_font = ImageFont.truetype(
            FONT_PATH,
            36
        )
    else:
        item_font = ImageFont.load_default()

    max_chars = 18

    line1 = item_text[:max_chars]
    line2 = item_text[
        max_chars:max_chars * 2
    ]

    draw.text(
        (margin, y),
        line1,
        fill="black",
        font=item_font
    )

    y += 46

    if line2:
        draw.text(
            (margin, y),
            line2,
            fill="black",
            font=item_font
        )

        y += 46

    else:
        y += 10

    # =========================
    # 企業名
    # =========================
    company_text = (
        f"企業名：{company_name}"
    )

    unit_text = ""

    if unit_number is not None:
        unit_text = f"個体No. {unit_number:03d}"

    company_max_width = usable_width

    if unit_text:
        company_max_width = max(260, usable_width - 165)

    company_font = fit_text(
        draw,
        company_text,
        FONT_PATH,
        company_max_width,
        24,
        18
    )

    draw.text(
        (margin, y),
        company_text,
        fill="black",
        font=company_font
    )

    if unit_text:
        unit_font = fit_text(
            draw,
            unit_text,
            FONT_PATH,
            155,
            22,
            16
        )
        unit_bbox = draw.textbbox(
            (0, 0),
            unit_text,
            font=unit_font
        )
        unit_width = unit_bbox[2] - unit_bbox[0]
        draw.text(
            (
                width_px
                - right_margin
                - unit_width,
                y
            ),
            unit_text,
            fill="black",
            font=unit_font
        )

    # =========================
    # バーコード
    # =========================
    if item_code:
        barcode_img = make_barcode_png(
            item_code
        )

        target_width = width_px - 20

        ratio = (
            target_width
            / barcode_img.width
        )

        target_height = int(
            barcode_img.height
            * ratio
        )

        target_height = int(
            target_height * 1.5
        )

        barcode_img = barcode_img.resize(
            (
                target_width,
                target_height
            )
        )

        x = int(
            (
                width_px
                - target_width
            )
            / 2
        )

        y_bar = (
            height_px
            - target_height
            - 8
        )

        img.paste(
            barcode_img,
            (x, y_bar)
        )

    return img


# =========================================================
# QL-820印刷
# =========================================================
def print_one_label(job):
    img = make_label_png(
        job
    ).convert("RGB")

    qlr = BrotherQLRaster(
        PRINTER_MODEL
    )

    qlr.exception_on_warning = True

    instructions = convert(
        qlr=qlr,
        images=[img],
        label=LABEL_SIZE,
        rotate="auto",
        threshold=70.0,
        dither=False,
        compress=False,
        red=RED_PRINT,
        dpi_600=False,
        hq=True,
        cut=CUT_LABEL,
    )

    send(
        instructions=instructions,
        printer_identifier=(
            f"tcp://{PRINTER_IP}"
        ),
        backend_identifier="network",
        blocking=True,
    )


# =========================================================
# 印刷ジョブ取得・更新
# =========================================================
def get_next_pending_job(conn):
    return fetch_one(
        conn,
        """
        SELECT *
        FROM print_jobs
        WHERE status = 'pending'
        ORDER BY requested_at ASC, id ASC
        LIMIT 1
        """
    )


def mark_printing(conn, job_id):
    execute_write(
        conn,
        """
        UPDATE print_jobs
        SET
            status = 'printing',
            error_message = NULL
        WHERE id = ?
          AND status = 'pending'
        """,
        [job_id]
    )

    conn.commit()


def mark_printed(conn, job_id):
    execute_write(
        conn,
        """
        UPDATE print_jobs
        SET
            status = 'printed',
            printed_at = CURRENT_TIMESTAMP,
            error_message = NULL
        WHERE id = ?
        """,
        [job_id]
    )

    conn.commit()


def mark_error(
    conn,
    job_id,
    error_message
):
    execute_write(
        conn,
        """
        UPDATE print_jobs
        SET
            status = 'error',
            error_message = ?
        WHERE id = ?
        """,
        [
            str(error_message)[:2000],
            job_id
        ]
    )

    conn.commit()


# =========================================================
# 1件のジョブを印刷
# =========================================================
def process_job(job):
    job_id = job["id"]

    quantity = max(
        1,
        int(job.get("quantity") or 1)
    )

    print(
        f"🖨️ 印刷開始："
        f"ジョブID={job_id} / "
        f"商品={job.get('item_name', '')} / "
        f"{quantity}枚"
    )

    for number in range(
        1,
        quantity + 1
    ):
        print(
            f"   {number}/{quantity}枚目を送信中..."
        )

        print_one_label(job)

    print(
        f"✅ 印刷完了："
        f"ジョブID={job_id} / "
        f"{quantity}枚"
    )


# =========================================================
# メインループ
# =========================================================
def main():
    if not PRINTER_IP:
        raise ValueError(
            "プリンターIPが設定されていません。"
        )

    print("=" * 60)
    print("🦈 SHARK 遠隔ラベル印刷ワーカー")
    print(f"プリンター：{PRINTER_MODEL}")
    print(f"IPアドレス：{PRINTER_IP}")
    print(f"確認間隔：{POLL_SECONDS}秒")
    print("=" * 60)

    while RUNNING:
        conn = None
        job = None

        try:
            conn = get_connection()

            job = get_next_pending_job(
                conn
            )

            if not job:
                time.sleep(
                    POLL_SECONDS
                )
                continue

            job_id = job["id"]

            mark_printing(
                conn,
                job_id
            )

            try:
                process_job(job)

                mark_printed(
                    conn,
                    job_id
                )

            except Exception as print_error:
                print(
                    f"❌ 印刷失敗："
                    f"ジョブID={job_id} / "
                    f"{print_error}"
                )

                mark_error(
                    conn,
                    job_id,
                    print_error
                )

        except Exception as worker_error:
            print(
                f"⚠️ ワーカー処理エラー "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}："
                f"{worker_error}"
            )

            time.sleep(
                POLL_SECONDS
            )

        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    print(
        "🦈 遠隔印刷ワーカーを終了しました。"
    )


if __name__ == "__main__":
    main()
