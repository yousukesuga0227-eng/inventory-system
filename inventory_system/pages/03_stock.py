import streamlit as st
from database import get_connection
from auth import check_login

check_login()
conn = get_connection()

st.title("入出庫登録")

# 案件取得
projects = conn.execute(
    "SELECT * FROM projects"
).fetchall()

# 商品取得
items = conn.execute(
    "SELECT * FROM items"
).fetchall()

# 案件辞書
project_map = {
    f"{p['code']} - {p['name']}": p['id']
    for p in projects
}

# 商品辞書
item_map = {
    f"{i['code']} - {i['name']}": i['id']
    for i in items
}

# 種別
stock_type = st.radio(
    "種別",
    ["入庫", "出庫"]
)

# 案件選択
selected_project = st.selectbox(
    "案件",
    list(project_map.keys())
)

# バーコード入力
barcode = st.text_input(
    "商品コード"
)

selected_item = None

for key in item_map.keys():

    if key.startswith(barcode):

        selected_item = key

        st.success(
            f"商品: {key}"
        )

        break

# 数量
qty = st.number_input(
    "数量",
    min_value=1,
    step=1
)

# 登録
if st.button("登録"):

    if not selected_item:

        st.error(
            "商品が見つかりません"
        )

    else:

        project_id = project_map[
            selected_project
        ]

        item_id = item_map[
            selected_item
        ]

        # 出庫時在庫チェック
        if stock_type == "出庫":

            stock = conn.execute(
                """
                SELECT
                    COALESCE(
                        SUM(qty),
                        0
                    )
                FROM stock_logs
                WHERE
                    project_id = ?
                    AND item_id = ?
                """,
                (
                    project_id,
                    item_id
                )
            ).fetchone()[0]

            if qty > stock:

                st.error(
                    f"在庫不足: {stock}"
                )

                st.stop()

            qty = -qty

        # DB登録
        conn.execute(
            """
            INSERT INTO stock_logs(
                project_id,
                item_id,
                qty,
                type
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                project_id,
                item_id,
                qty,
                stock_type
            )
        )

        conn.commit()

        st.success(
            f"{stock_type}登録完了"
        )