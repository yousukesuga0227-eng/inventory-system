import streamlit as st
from database import get_connection
from auth import check_login
import os
from io import BytesIO
from datetime import datetime
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Image,
    Table,
    TableStyle
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.graphics.barcode import code128
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet
from collections import Counter

check_login()
conn = get_connection()

st.title("📄 出荷準備")
login_name = st.session_state.get(
    "display_name",
    st.session_state.username
)

st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

font_path = os.path.join(BASE_DIR, "fonts", "NotoSansJP-VariableFont_wght.ttf")
logo_path = os.path.join(BASE_DIR, "20260608-logo.png")

pdfmetrics.registerFont(TTFont("NotoSansJP", font_path))

# =====================
# セッション初期化
# =====================

if "shipping_scanned_codes" not in st.session_state:
    st.session_state.shipping_scanned_codes = []

if "shipping_barcode_input" not in st.session_state:
    st.session_state.shipping_barcode_input = ""

if "shipping_selected_project_id" not in st.session_state:
    st.session_state.shipping_selected_project_id = None

if "shipping_mode" not in st.session_state:
    st.session_state.shipping_mode = "案件あり"

if "shipping_qty_adjustments" not in st.session_state:
    st.session_state.shipping_qty_adjustments = {}


def reset_shipping_scan():
    st.session_state.shipping_scanned_codes = []
    st.session_state.shipping_barcode_input = ""
    st.session_state.shipping_qty_adjustments = {}


def add_barcode():
    barcode_text = st.session_state.shipping_barcode_input.strip()

    if not barcode_text:
        return

    if "_" in barcode_text:
        barcode_text = barcode_text.split("_")[-1]

    st.session_state.shipping_scanned_codes.append(barcode_text)
    st.session_state.shipping_barcode_input = ""


# =====================
# PDF作成関数
# =====================

def create_shipping_pdf(
    title,
    info_data,
    pdf_items,
    file_prefix
):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=10 * mm,
        leftMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm
    )

    styles = getSampleStyleSheet()

    title_style = styles["Title"]
    title_style.fontName = "NotoSansJP"

    normal_style = styles["BodyText"]
    normal_style.fontName = "NotoSansJP"
    normal_style.fontSize = 8
    normal_style.leading = 10

    elements = []

    if os.path.exists(logo_path):
        elements.append(
            Image(
                logo_path,
                width=75 * mm,
                height=18 * mm
            )
        )
        elements.append(Spacer(1, 8))

    elements.append(Paragraph(title, title_style))
    elements.append(Spacer(1, 10))

    info_table = Table(
        info_data,
        colWidths=[
            25 * mm,
            90 * mm,
            25 * mm,
            60 * mm
        ]
    )

    info_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "NotoSansJP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("BACKGROUND", (2, 0), (2, -1), colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    elements.append(info_table)
    elements.append(Spacer(1, 15))

    table_data = [
        [
            "No.",
            "商品コード",
            "商品名",
            "数量",
            "バーコード",
            "ピッキング",
            "検品",
            "積込"
        ]
    ]

    for index, item in enumerate(pdf_items, start=1):
        barcode_obj = code128.Code128(
            str(item["code"]),
            barHeight=18 * mm,
            barWidth=0.45,
            humanReadable=True
        )

        table_data.append(
            [
                str(index),
                item["code"],
                Paragraph(item["name"], normal_style),
                str(item["quantity"]),
                barcode_obj,
                "□",
                "□",
                "□"
            ]
        )

    item_table = Table(
        table_data,
        colWidths=[
            10 * mm,
            35 * mm,
            65 * mm,
            15 * mm,
            65 * mm,
            20 * mm,
            18 * mm,
            18 * mm
        ],
        repeatRows=1
    )

    item_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "NotoSansJP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("ALIGN", (0, 0), (1, -1), "CENTER"),
                ("ALIGN", (3, 1), (3, -1), "CENTER"),
                ("ALIGN", (5, 1), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    elements.append(item_table)
    elements.append(Spacer(1, 18))

    memo_table = Table(
        [
            ["備考", ""],
            ["確認欄", "□ ピッキング完了　　□ 検品完了　　□ 積込完了"]
        ],
        colWidths=[
            25 * mm,
            215 * mm
        ],
        rowHeights=[
            20 * mm,
            12 * mm
        ]
    )

    memo_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "NotoSansJP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    elements.append(memo_table)

    doc.build(elements)

    return buffer.getvalue(), f"{file_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"


# =====================
# 出荷モード選択
# =====================

mode = st.radio(
    "出荷指示書の作成方法",
    [
        "案件あり",
        "案件なし"
    ],
    horizontal=True
)

if st.session_state.shipping_mode != mode:
    st.session_state.shipping_mode = mode
    reset_shipping_scan()
    st.rerun()

st.write("---")

# =====================
# 案件ありモード
# =====================

if mode == "案件あり":

    st.subheader("案件あり：案件の商品を検品して出荷指示書を作成")

    projects = conn.execute(
        """
        SELECT
            p.id,
            p.code,
            p.name,
            p.shipping_date,
            c.code AS company_code,
            c.name AS company_name
        FROM projects p
        LEFT JOIN project_companies pc
            ON p.id = pc.project_id
        LEFT JOIN companies c
            ON pc.company_id = c.id
        WHERE COALESCE(p.is_hidden, FALSE) = FALSE
        ORDER BY p.name
        """
    ).fetchall()

    projects = [dict(row) for row in projects]

    if not projects:
        st.warning("案件が登録されていません")
        st.stop()

    project_options = {
        f"{p['code']}：{p['name']}": p["id"]
        for p in projects
    }

    selected_project = st.selectbox(
        "案件選択",
        list(project_options.keys())
    )

    project_id = project_options[selected_project]

    if st.session_state.shipping_selected_project_id != project_id:
        st.session_state.shipping_selected_project_id = project_id
        reset_shipping_scan()
        st.rerun()

    project = conn.execute(
        """
        SELECT
            p.id,
            p.code,
            p.name,
            p.shipping_date,
            c.code AS company_code,
            c.name AS company_name
        FROM projects p
        LEFT JOIN project_companies pc
            ON p.id = pc.project_id
        LEFT JOIN companies c
            ON pc.company_id = c.id
        WHERE p.id = ?
          AND COALESCE(p.is_hidden, FALSE) = FALSE
        """,
        (project_id,)
    ).fetchone()

    project = dict(project)

    items = conn.execute(
        """
        SELECT
            code,
            name,
            COALESCE(required_quantity, 1) AS required_quantity
        FROM items
        WHERE project_id = ?
          AND COALESCE(is_active, TRUE) = TRUE
        ORDER BY code
        """,
        (project_id,)
    ).fetchall()

    items = [dict(row) for row in items]

    if not items:
        st.warning("この案件には有効な商品が登録されていません")
        st.stop()

    item_map = {
        str(item["code"]): item["name"]
        for item in items
    }

    quantity_map = {
        str(item["code"]): int(item["required_quantity"])
        for item in items
    }

    st.info(
        f"選択案件：{project['code']} / {project['name']}　商品数：{len(items)}"
    )

    # ---- ピッキングリスト ----
    if st.button("📋 ピッキングリスト作成"):

        pdf_items = [
            {
                "code": item["code"],
                "name": item["name"],
                "quantity": item["required_quantity"]
            }
            for item in items
        ]

        info_data = [
            ["企業名", project.get("company_name") or "", "企業コード", project.get("company_code") or ""],
            ["案件名", project["name"], "案件コード", project["code"]],
            ["出荷予定日", str(project["shipping_date"]), "発行日", datetime.now().strftime("%Y/%m/%d")],
            ["発行者", st.session_state.username, "用途", "出荷前ピッキング"],
        ]

        pdf_data, file_name = create_shipping_pdf(
            "ピッキングリスト",
            info_data,
            pdf_items,
            f"ピッキングリスト_{project['code']}"
        )

        st.download_button(
            label="⬇ ピッキングリストPDFダウンロード",
            data=pdf_data,
            file_name=file_name,
            mime="application/pdf"
        )

    st.write("---")

    # ---- バーコード検品 ----
    st.subheader("バーコード検品")

    st.text_input(
        "バーコード読み取り",
        key="shipping_barcode_input",
        placeholder="ここを1回クリックしてからスキャン",
        on_change=add_barcode
    )

    if st.button("読み取りリセット"):
        reset_shipping_scan()
        st.rerun()

    scanned_codes = st.session_state.shipping_scanned_codes
    scan_counter = Counter(scanned_codes)

    required_total = sum(quantity_map.values())

    checked_total = sum(
        min(scan_counter.get(code, 0), quantity_map[code])
        for code in quantity_map
    )

    progress_rate = checked_total / required_total if required_total > 0 else 0

    st.subheader("検品進捗")
    st.progress(progress_rate)
    st.metric("検品数", f"{checked_total} / {required_total}")

    check_status_list = []

    for code, name in item_map.items():

        required_qty = quantity_map[code]
        scanned_qty = scan_counter.get(code, 0)

        if scanned_qty == required_qty:
            status = "✅ OK"
        elif scanned_qty == 0:
            status = "未検品"
        elif scanned_qty < required_qty:
            status = "🟡 不足"
        else:
            status = "🔴 読み過ぎ"

        check_status_list.append(
            {
                "商品コード": code,
                "商品名": name,
                "必要個数": required_qty,
                "読取個数": scanned_qty,
                "状態": status
            }
        )

    st.dataframe(
        check_status_list,
        width="stretch",
        hide_index=True
    )

    valid_results = []
    invalid_results = []

    for index, scanned_code in enumerate(scanned_codes, start=1):

        if scanned_code in item_map:
            valid_results.append(
                {
                    "No.": index,
                    "商品コード": scanned_code,
                    "商品名": item_map[scanned_code],
                    "判定": "OK"
                }
            )
        else:
            invalid_results.append(
                {
                    "No.": index,
                    "商品コード": scanned_code,
                    "商品名": "",
                    "判定": "NG",
                    "理由": "この案件の商品ではありません"
                }
            )

    if scanned_codes:
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("読み取り数", len(scanned_codes))

        with col2:
            st.metric("OK", len(valid_results))

        with col3:
            st.metric("NG", len(invalid_results))

    if valid_results:
        st.success("OKの商品")
        st.dataframe(valid_results, width="stretch", hide_index=True)

    if invalid_results:
        st.error("NGの商品があります")
        st.dataframe(invalid_results, width="stretch", hide_index=True)

    st.write("---")
    st.subheader("今回出荷数の確認・修正")
    st.info("全てスキャン後、必要に応じて『今回出荷数』を修正してください。PDFには今回出荷数が反映されます。")

    shipping_rows = []
    pdf_items = []

    for code, name in item_map.items():
        required_qty = quantity_map[code]
        scanned_qty = scan_counter.get(code, 0)

        default_qty = st.session_state.shipping_qty_adjustments.get(code, scanned_qty)

        ship_qty = st.number_input(
            f"{code} / {name}",
            min_value=0,
            value=int(default_qty),
            step=1,
            key=f"ship_qty_{code}"
        )

        st.session_state.shipping_qty_adjustments[code] = ship_qty

        diff = ship_qty - required_qty

        if diff == 0:
            status = "✅ OK"
        elif diff < 0:
            status = "🟡 不足"
        else:
            status = "🔴 超過"

        shipping_rows.append(
            {
                "商品コード": code,
                "商品名": name,
                "必要個数": required_qty,
                "読取個数": scanned_qty,
                "今回出荷数": ship_qty,
                "差異": diff,
                "状態": status
            }
        )

        if ship_qty > 0:
            pdf_items.append(
                {
                    "code": code,
                    "name": name,
                    "quantity": ship_qty
                }
            )

    st.dataframe(
        shipping_rows,
        width="stretch",
        hide_index=True
    )

    can_create_pdf = (
        len(pdf_items) > 0
        and len(invalid_results) == 0
    )

    if invalid_results:
        st.warning("NGの商品があるため、出荷指示書は作成できません。")
    elif not pdf_items:
        st.warning("今回出荷数が1以上の商品がありません。")
    else:
        st.success("今回出荷数をもとに出荷指示書を作成できます。")

    st.write("---")

    if st.button("📄 出荷指示書作成", disabled=not can_create_pdf):

        info_data = [
            ["企業名", project.get("company_name") or "", "企業コード", project.get("company_code") or ""],
            ["案件名", project["name"], "案件コード", project["code"]],
            ["出荷予定日", str(project["shipping_date"]), "発行日", datetime.now().strftime("%Y/%m/%d")],
            ["発行者", st.session_state.username, "検品状態", "数量修正済"],
        ]

        pdf_data, file_name = create_shipping_pdf(
            "出荷指示書",
            info_data,
            pdf_items,
            f"出荷指示書_{project['code']}"
        )

        st.download_button(
            label="⬇ PDFダウンロード",
            data=pdf_data,
            file_name=file_name,
            mime="application/pdf"
        )


# =====================
# 案件なしモード
# =====================

else:

    st.subheader("案件なし：スキャンした商品だけで出荷指示書を作成")

    st.info(
        "在庫品など、案件に紐づかない商品はここでスキャンして出荷指示書を作成します。"
    )

    st.text_input(
        "バーコード読み取り",
        key="shipping_barcode_input",
        placeholder="ここを1回クリックしてからスキャン",
        on_change=add_barcode
    )

    if st.button("読み取りリセット"):
        reset_shipping_scan()
        st.rerun()

    scanned_codes = st.session_state.shipping_scanned_codes
    scan_counter = Counter(scanned_codes)

    valid_items = {}
    invalid_results = []

    for index, scanned_code in enumerate(scanned_codes, start=1):

        item = conn.execute(
            """
            SELECT
                code,
                name
            FROM items
            WHERE code = ?
              AND COALESCE(is_active, TRUE) = TRUE
            LIMIT 1
            """,
            (scanned_code,)
        ).fetchone()

        if item:
            item = dict(item)
            valid_items[scanned_code] = item["name"]
        else:
            invalid_results.append(
                {
                    "No.": index,
                    "商品コード": scanned_code,
                    "判定": "NG",
                    "理由": "商品マスタに存在しません"
                }
            )

    valid_results = []

    for code, qty in scan_counter.items():
        if code in valid_items:
            valid_results.append(
                {
                    "商品コード": code,
                    "商品名": valid_items[code],
                    "数量": qty,
                    "判定": "OK"
                }
            )

    st.subheader("読み取り結果")

    if not scanned_codes:
        st.info("まだバーコードが読み取られていません")
    else:
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("読み取り数", len(scanned_codes))

        with col2:
            st.metric("商品種類", len(valid_results))

        with col3:
            st.metric("NG", len(invalid_results))

    if valid_results:
        st.success("出荷指示書に載せる商品")
        st.dataframe(
            valid_results,
            width="stretch",
            hide_index=True
        )

    if invalid_results:
        st.error("NGの商品があります")
        st.dataframe(
            invalid_results,
            width="stretch",
            hide_index=True
        )

    can_create_pdf = (
        len(scanned_codes) > 0
        and len(valid_results) > 0
        and len(invalid_results) == 0
    )

    if not can_create_pdf:
        st.warning("NGが無く、商品が1件以上読み取られた時だけ出荷指示書を作成できます。")
    else:
        st.success("出荷指示書を作成できます。")

    st.write("---")

    if st.button("📄 在庫品 出荷指示書作成", disabled=not can_create_pdf):

        pdf_items = [
            {
                "code": row["商品コード"],
                "name": row["商品名"],
                "quantity": row["数量"]
            }
            for row in valid_results
        ]

        first_code = pdf_items[0]["code"] if pdf_items else "STOCK"
        company_code = first_code.split("-")[0] if "-" in first_code else ""

        company = None

        if company_code:
            company = conn.execute(
                """
                SELECT code, name
                FROM companies
                WHERE code = ?
                LIMIT 1
                """,
                (company_code,)
            ).fetchone()

        company_name = ""
        company_label_code = ""

        if company:
            company = dict(company)
            company_name = company["name"]
            company_label_code = company["code"]

        info_data = [
             ["企業名", company_name, "企業コード", company_label_code],
             ["案件名", "案件なし", "案件コード", "-"],
            ["出荷予定日", datetime.now().strftime("%Y/%m/%d"), "発行日", datetime.now().strftime("%Y/%m/%d")],
             ["発行者", login_name, "読取商品数", str(len(pdf_items))],
]

        pdf_data, file_name = create_shipping_pdf(
            "在庫品 出荷指示書",
            info_data,
            pdf_items,
            f"在庫品出荷指示書_{datetime.now().strftime('%Y%m%d')}"
        )

        st.download_button(
            label="⬇ PDFダウンロード",
            data=pdf_data,
            file_name=file_name,
            mime="application/pdf"
        )

conn.close()