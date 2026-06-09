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


check_login()

conn = get_connection()

st.title("📄 出荷指示書")
st.success(
    f"ログイン中：{st.session_state.username}"
)

# =====================
# パス設定
# =====================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        __file__
    )
)

font_path = os.path.join(
    BASE_DIR,
    "fonts",
    "NotoSansJP-VariableFont_wght.ttf"
)

logo_path = os.path.join(
    BASE_DIR,
    "20260608-logo.png"
)

# フォント登録
pdfmetrics.registerFont(
    TTFont(
        "NotoSansJP",
        font_path
    )
)

# =====================
# セッション初期化
# =====================

if "shipping_scanned_codes" not in st.session_state:
    st.session_state.shipping_scanned_codes = []

if "shipping_barcode_input" not in st.session_state:
    st.session_state.shipping_barcode_input = ""

if "shipping_selected_project_id" not in st.session_state:
    st.session_state.shipping_selected_project_id = None


# =====================
# バーコード追加処理
# =====================

def add_barcode():

    barcode_text = st.session_state.shipping_barcode_input.strip()

    if not barcode_text:
        return

    # 念のため、古い形式「案件コード_商品コード」にも対応
    # 商品コードにハイフンが入る可能性があるので、ハイフンでは分割しない
    if "_" in barcode_text:
        barcode_text = barcode_text.split("_")[-1]

    st.session_state.shipping_scanned_codes.append(
        barcode_text
    )

    # 入力欄を空にする
    st.session_state.shipping_barcode_input = ""


# =====================
# 案件取得
# =====================

projects = conn.execute(
    """
    SELECT *
    FROM projects
    ORDER BY name
    """
).fetchall()

if not projects:

    st.warning(
        "案件が登録されていません"
    )

    st.stop()

project_options = {
    project["name"]: project["id"]
    for project in projects
}

selected_project = st.selectbox(
    "案件選択",
    list(project_options.keys())
)

project_id = project_options[
    selected_project
]

# 案件変更時は読み取りリストをクリア
if st.session_state.shipping_selected_project_id != project_id:

    st.session_state.shipping_selected_project_id = project_id
    st.session_state.shipping_scanned_codes = []
    st.session_state.shipping_barcode_input = ""

project = conn.execute(
    """
    SELECT *
    FROM projects
    WHERE id = ?
    """,
    (project_id,)
).fetchone()

project_code = project["code"]
project_name = project["name"]
shipping_date = (
    project["shipping_date"]
    if "shipping_date" in project.keys()
    else ""
)

# =====================
# 選択案件の商品取得
# =====================

items = conn.execute(
    """
    SELECT
        code,
        name
    FROM items
    WHERE project_id = ?
    ORDER BY code
    """,
    (project_id,)
).fetchall()

if not items:

    st.warning(
        "この案件には商品が登録されていません"
    )

    st.stop()

item_map = {
    str(item["code"]): item["name"]
    for item in items
}

selected_count = len(items)

st.write(
    "選択案件の商品数 =",
    selected_count
)

st.write("---")

# =====================
# バーコード読み取り
# =====================

st.subheader("バーコード検品")

st.info(
    "バーコード入力欄を1回クリックしてから、バーコードリーダーで連続読み取りしてください。読み取り後Enter送信で自動追加されます。"
)

st.text_input(
    "バーコード読み取り",
    key="shipping_barcode_input",
    placeholder="ここを1回クリックしてからスキャン",
    on_change=add_barcode
)

col_clear1, col_clear2 = st.columns([1, 5])

with col_clear1:

    if st.button("読み取りリセット"):

        st.session_state.shipping_scanned_codes = []
        st.session_state.shipping_barcode_input = ""

        st.rerun()

scanned_codes = st.session_state.shipping_scanned_codes

# =====================
# 読み取り結果判定
# =====================

st.subheader("読み取り結果")

if not scanned_codes:

    st.info(
        "まだバーコードが読み取られていません"
    )

    valid_results = []
    invalid_results = []
    duplicate_codes = []

else:

    valid_results = []
    invalid_results = []
    duplicate_codes = []

    seen_codes = set()

    for index, scanned_code in enumerate(
        scanned_codes,
        start=1
    ):

        is_duplicate = scanned_code in seen_codes

        if is_duplicate:
            duplicate_codes.append(scanned_code)

        seen_codes.add(scanned_code)

        if scanned_code in item_map:

            valid_results.append(
                {
                    "No.": index,
                    "商品コード": scanned_code,
                    "商品名": item_map[scanned_code],
                    "判定": "OK",
                    "重複": "あり" if is_duplicate else ""
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

    ok_count = len(valid_results)
    ng_count = len(invalid_results)
    duplicate_count = len(duplicate_codes)

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "読み取り数",
            len(scanned_codes)
        )

    with col2:
        st.metric(
            "OK",
            ok_count
        )

    with col3:
        st.metric(
            "NG",
            ng_count
        )

    if valid_results:

        st.success(
            "OKの商品"
        )

        st.dataframe(
            valid_results,
            use_container_width=True,
            hide_index=True
        )

    if invalid_results:

        st.error(
            "NGの商品があります。案件違い、または未登録の商品コードです。"
        )

        st.dataframe(
            invalid_results,
            use_container_width=True,
            hide_index=True
        )

    if duplicate_count > 0:

        st.warning(
            f"重複読み取りがあります：{duplicate_count}件"
        )

# =====================
# PDF作成可否
# =====================

can_create_pdf = (
    len(scanned_codes) > 0
    and len(invalid_results) == 0
)

if not can_create_pdf:

    st.warning(
        "出荷指示書は、読み取り結果がすべてOKになった時だけ作成できます。"
    )

else:

    st.success(
        "すべてOKです。出荷指示書を作成できます。"
    )

st.write("---")

# =====================
# PDF作成
# =====================

if st.button(
    "📄 出荷指示書作成",
    disabled=not can_create_pdf
):

    # 重複分はPDFでは1商品として扱う
    pdf_codes = []

    for code in scanned_codes:

        if code not in pdf_codes:
            pdf_codes.append(code)

    pdf_items = [
        {
            "code": code,
            "name": item_map[code]
        }
        for code in pdf_codes
        if code in item_map
    ]

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

    # =====================
    # ロゴ
    # =====================

    if os.path.exists(logo_path):

        elements.append(
            Image(
                logo_path,
                width=75 * mm,
                height=18 * mm
            )
        )

        elements.append(
            Spacer(1, 8)
        )

    # =====================
    # タイトル
    # =====================

    elements.append(
        Paragraph(
            "出荷指示書",
            title_style
        )
    )

    elements.append(
        Spacer(1, 10)
    )

    # =====================
    # 案件情報テーブル
    # =====================

    info_data = [
        [
            "案件名",
            project_name,
            "案件コード",
            project_code
        ],
        [
            "出荷予定日",
            str(shipping_date),
            "発行日",
            datetime.now().strftime("%Y/%m/%d")
        ],
        [
            "発行者",
            st.session_state.username,
            "確認者",
            ""
        ],
        [
            "読取商品数",
            str(len(pdf_items)),
            "検品状態",
            "OK"
        ],
    ]

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
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    elements.append(info_table)

    elements.append(
        Spacer(1, 15)
    )

    # =====================
    # 商品一覧テーブル
    # =====================

    table_data = [
        [
            "No.",
            "商品コード",
            "商品名",
            "バーコード",
            "ピッキング",
            "検品",
            "積込"
        ]
    ]

    for index, item in enumerate(
        pdf_items,
        start=1
    ):

        item_code = item["code"]
        item_name = item["name"]

        barcode_value = str(item_code)

        barcode = code128.Code128(
            barcode_value,
            barHeight=18 * mm,
            barWidth=0.45,
            humanReadable=True
        )

        table_data.append(
            [
                str(index),
                item_code,
                Paragraph(item_name, normal_style),
                barcode,
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
            70 * mm,
            65 * mm,
            22 * mm,
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
                ("ALIGN", (4, 1), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    elements.append(item_table)

    elements.append(
        Spacer(1, 18)
    )

    # =====================
    # 備考・確認欄
    # =====================

    memo_table = Table(
        [
            [
                "備考",
                ""
            ],
            [
                "確認欄",
                "□ ピッキング完了　　□ 検品完了　　□ 積込完了"
            ]
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
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )

    elements.append(memo_table)

    doc.build(elements)

    pdf_data = buffer.getvalue()

    st.download_button(
        label="⬇ PDFダウンロード",
        data=pdf_data,
        file_name=f"出荷指示書_{project_code}.pdf",
        mime="application/pdf"
    )