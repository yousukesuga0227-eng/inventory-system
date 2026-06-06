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

# フォント登録
pdfmetrics.registerFont(
    TTFont(
        "NotoSansJP",
        font_path
    )
)

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

selected_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM items
    WHERE project_id = ?
    """,
    (project_id,)
).fetchone()[0]

st.write(
    "選択案件の商品数 =",
    selected_count
)

# =====================
# PDF作成
# =====================

if st.button("📄 出荷指示書作成"):

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
    shipping_date = project["shipping_date"] if "shipping_date" in project.keys() else ""

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

    logo_path = os.path.join(
        BASE_DIR,
        "logo.png"
    )

    if os.path.exists(logo_path):

        elements.append(
            Image(
                logo_path,
                width=55 * mm,
                height=8 * mm
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

    for index, item in enumerate(items, start=1):

        item_code = item["code"]
        item_name = item["name"]

        # 出荷指示書のバーコードは商品コードのみ
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