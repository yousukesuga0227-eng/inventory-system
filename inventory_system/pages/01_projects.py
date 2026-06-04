import streamlit as st
import os
import barcode
from datetime import datetime
from barcode.writer import ImageWriter
from database import get_connection
conn = get_connection()

from auth import check_admin

check_admin()

st.title("案件管理")
st.success(
    f"ログイン中：{st.session_state.username}"
)

code = st.text_input("案件コード")
name = st.text_input("案件名")

receive_date = st.date_input(
    "入庫予定日"
)

shipping_date = st.date_input(
    "出荷予定日"
)

status = st.selectbox(
    "案件状態",
    [
        "未着荷",
        "入庫済",
        "出荷待ち",
        "出荷済",
        "完了"
    ]
)

memo = st.text_area(
    "備考"
)

if st.button("案件登録"):

    if not code or not name:

        st.warning(
            "案件コードと案件名を入力してください"
        )

    else:

        conn.execute(
            """
            INSERT INTO projects(
                code,
                name,
                receive_date,
                shipping_date,
                status,
                memo,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                name,
                str(receive_date),
                str(shipping_date),
                status,
                memo,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        )
        conn.commit()

        # フォルダ作成
        os.makedirs(
            "barcodes/projects",
            exist_ok=True
        )

        # Code128
        barcode_class = barcode.get_barcode_class(
            "code128"
        )

        barcode_obj = barcode_class(
            code,
            writer=ImageWriter()
        )

        # 保存
        barcode_obj.save(
            f"barcodes/projects/{code}"
        )

        st.success(
            "案件登録 + バーコード生成完了"
        )


sort_option = st.selectbox(
    "並び順",
    [
        "ID順",
        "案件コード順",
        "案件名順"
    ]
)

order_by = "id"

if sort_option == "案件コード順":

    order_by = "code"

elif sort_option == "案件名順":

    order_by = "name"

st.subheader("案件一覧")

# 検索
search_text = st.text_input(
    "案件検索"
)

query = """
SELECT *
FROM projects
WHERE 1=1
"""

params = []

# 検索条件
if search_text:

    query += """
    AND (
        code LIKE ?
        OR name LIKE ?
    )
    """

    params.append(
        f"%{search_text}%"
    )

    params.append(
        f"%{search_text}%"
    )

# 並び順
query += f"""
ORDER BY {order_by}
"""

rows = conn.execute(
    query,
    params
).fetchall()

project_list = []

for row in rows:

    project_list.append(
        {
            "ID": row["id"],
            "案件コード": row["code"],
            "案件名": row["name"],
            "入庫予定": row["receive_date"],
            "出荷予定": row["shipping_date"],
            "状態": row["status"]
        }
    )

st.dataframe(
    project_list,
    use_container_width=True
)