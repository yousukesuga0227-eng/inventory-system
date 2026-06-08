import streamlit as st
import os
import barcode
from barcode.writer import ImageWriter
from database import get_connection, log_action
from auth import check_admin
import pandas as pd

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

    else:

        # 商品登録
        cursor = conn.execute(
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

        new_item_id = cursor.lastrowid

        conn.commit()

        log_action(
            st.session_state.username,
            "商品登録",
            "items",
            new_item_id,
            name,
            f"商品コード: {code} / 案件ID: {project_id}"
        )

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

        # 保存フォルダ作成
        os.makedirs(
            "barcodes/project_items",
            exist_ok=True
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

order_by = "items.id"

if sort_option == "商品コード順":

    order_by = "items.code"

elif sort_option == "商品名順":

    order_by = "items.name"

st.subheader("商品一覧")

st.subheader("シール印刷用CSV出力")

# 案件で絞り込み
label_project = st.selectbox(
    "CSV出力する案件",
    ["すべて"] + list(project_options.keys())
)

label_query = """
SELECT
    items.code AS 商品コード,
    items.name AS 商品名,
    projects.name AS 案件名,
FROM items
LEFT JOIN projects
    ON items.project_id = projects.id
WHERE 1=1
"""

label_params = []

if label_project != "すべて":
    label_query += """
    AND projects.name = ?
    """
    label_params.append(label_project)

label_query += """
ORDER BY projects.name, items.code
"""

label_rows = conn.execute(
    label_query,
    label_params
).fetchall()

label_list = []

for row in label_rows:
    label_list.append(
        {
            "商品コード": row["商品コード"],
            "商品名": row["商品名"],
            "案件名": row["案件名"],
            "案件コード": row["案件コード"],
            "バーコード文字": f'{row["商品コード"]}'
        }
    )

df_labels = pd.DataFrame(label_list)

if label_project == "すべて":
    st.info(
        f"CSV出力対象：全案件 / {len(df_labels)}件"
    )
else:
    st.info(
        f"CSV出力対象：{label_project} / {len(df_labels)}件"
    )

csv = df_labels.to_csv(
    index=False
).encode("cp932", errors="ignore")

st.download_button(
    label="シール印刷用CSVを出力",
    data=csv,
    file_name="label_print.csv",
    mime="text/csv",
    disabled=df_labels.empty
)

if df_labels.empty:
    st.info("出力できる商品がありません")

# 検索
search_text = st.text_input(
    "商品検索"
)

query = """
SELECT
    items.id AS id,
    projects.name AS project_name,
    items.code AS code,
    items.name AS name
FROM items
LEFT JOIN projects
    ON items.project_id = projects.id
WHERE 1=1
"""

params = []

# 検索条件
if search_text:

    query += """
    AND (
    items.code LIKE ?
    OR items.name LIKE ?
    OR projects.name LIKE ?
)
    """

    params.append(
        f"%{search_text}%"
    )

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
        "案件名": row["project_name"],
        "商品コード": row["code"],
        "商品名": row["name"]
    }
     )

st.dataframe(
    item_list,
    width="stretch"
)