import streamlit as st
import os
import barcode
from barcode.writer import ImageWriter
from database import get_connection
from auth import check_admin

check_admin()


conn = get_connection()

st.title("商品管理")
st.success(
    f"ログイン中：{st.session_state.username}"
)

# 案件一覧取得
projects = conn.execute(
    """
    SELECT *
    FROM projects
    ORDER BY name
    """
).fetchall()

# 商品コード
code = st.text_input(
    "商品コード",
    key="new_item_code"
)
# 商品名
name = st.text_input(
    "商品名",
    key="new_item_name"
)

# 案件選択
project_options = {
    project["name"]: project["id"]
    for project in projects
}

if not project_options:

    st.warning(
        "先に案件を登録してください"
    )

    st.stop()
    

selected_project = st.selectbox(
    "案件選択",
    list(project_options.keys())
)

project_id = project_options[
    selected_project
]

if st.button("商品登録"):

    if not code or not name:

        st.warning(
            "商品コードと商品名を入力してください"
        )

    # 商品登録
    conn.execute(
        """
        INSERT INTO items(
            code,
            name,
            project_id
        )
        VALUES (?, ?, ?)
        """,
        (
            code,
            name,
            project_id
        )
    )

    conn.commit()

    # 案件コード取得
    project_row = conn.execute(
        """
        SELECT *
        FROM projects
        WHERE id = ?
        """,
        (project_id,)
    ).fetchone()

    project_code = project_row["code"]

    # 案件 + 商品コード
    barcode_data = (
        f"{project_code}_{code}"
    )

    # バーコード生成
    barcode_class = barcode.get_barcode_class(
        "code128"
    )

    barcode_obj = barcode_class(
        barcode_data,
        writer=ImageWriter()
    )

    # 保存
    barcode_obj.save(
        f"barcodes/project_items/{barcode_data}"
    )

    st.success(
        "商品 + 案件バーコード生成完了"
    )


sort_option = st.selectbox(
    "並び順",
    [
        "ID順",
        "商品コード順",
"商品名順"
    ]
)

order_by = "id"

if sort_option == "商品コード順":

    order_by = "code"

elif sort_option == "商品名順":

    order_by = "name"

st.subheader("商品一覧")

# 検索
search_text = st.text_input(
    "商品検索"
)

query = """
SELECT *
FROM items
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

item_list = []

for row in rows:

    item_list.append(
        {
            "ID": row["id"],
            "商品コード": row["code"],
            "商品名": row["name"]
        }
    )

st.dataframe(
    item_list,
    use_container_width=True
)