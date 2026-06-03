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
Image
)

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from reportlab.lib.styles import getSampleStyleSheet

check_login()

conn = get_connection()

st.title("📄 出荷指示書")

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

st.write("フォント登録OK")
st.write("案件取得OK")

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

# ←ここに追加

st.write("案件数 =", len(projects))
st.write(projects)

if projects:

    project_id = projects[0]["id"]

    item_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM items
        WHERE project_id = ?
        """,
        (project_id,)
    ).fetchone()[0]

    st.write("商品数 =", item_count)

# ここから元のコード

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

# 確認用
st.write("選択案件ID =", project_id)

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

    buffer = BytesIO()

    doc = SimpleDocTemplate(buffer)

    styles = getSampleStyleSheet()

    title_style = styles["Title"]
    title_style.fontName = "NotoSansJP"

    normal_style = styles["BodyText"]
    normal_style.fontName = "NotoSansJP"

    elements = []

    elements.append(
        Paragraph(
            "出荷指示書",
            title_style
        )
    )

    elements.append(
        Spacer(1, 20)
    )

    elements.append(
        Paragraph(
            f"案件名：{project_name}",
            normal_style
        )
    )

    # ↓↓↓ここから最後まで全部インデント↓↓↓

elements.append(
    Paragraph(
        f"案件コード：{project_code}",
        normal_style
    )
)

elements.append(
    Paragraph(
        f"発行日：{datetime.now().strftime('%Y/%m/%d')}",
        normal_style
    )
)

elements.append(
    Spacer(1, 20)
)

# =====================
# 商品一覧
# =====================

for item in items:

    item_code = item["code"]
    item_name = item["name"]

    barcode_file = os.path.join(
        BASE_DIR,
        "barcodes",
        "project_items",
        f"{project_code}_{item_code}.png"
    )

    elements.append(
        Paragraph(
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            normal_style
        )
    )

    elements.append(
        Spacer(1, 10)
    )

    elements.append(
        Paragraph(
            f"商品名：{item_name}",
            normal_style
        )
    )

    elements.append(
        Paragraph(
            f"商品コード：{item_code}",
            normal_style
        )
    )

    elements.append(
        Spacer(1, 10)
    )

    if os.path.exists(barcode_file):

        elements.append(
            Image(
                barcode_file,
                width=250,
                height=70
            )
        )

    else:

        elements.append(
            Paragraph(
                "バーコード画像なし",
                normal_style
            )
        )

    elements.append(
        Spacer(1, 20)
    )

# =====================
# PDF生成
# =====================

    doc.build(elements)

    pdf_data = buffer.getvalue()

    st.success("PDF作成完了")

    st.download_button(
        label="⬇ PDFダウンロード",
        data=pdf_data,
        file_name=f"出荷指示書_{project_code}.pdf",
        mime="application/pdf"
    )