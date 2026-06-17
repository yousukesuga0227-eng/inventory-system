import streamlit as st
import pandas as pd
from database import get_connection
from auth import check_login

check_login()
conn = get_connection()

st.set_page_config(
    layout="wide"
)

st.title("棚卸")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
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
    st.warning("先に案件を登録してください")
    st.stop()

project_options = {
    f"{p['code']} - {p['name']}": p["id"]
    for p in projects
}

selected_project = st.selectbox(
    "棚卸する案件",
    list(project_options.keys())
)

project_id = project_options[selected_project]

search_text = st.text_input(
    "商品コード / 商品名で検索"
)

# =====================
# 棚卸対象取得
# =====================

query = """
SELECT
    i.id AS item_id,
    p.code AS project_code,
    p.name AS project_name,
    i.code AS item_code,
    i.name AS item_name,
    COALESCE(SUM(s.qty), 0) AS current_qty
FROM items i

LEFT JOIN projects p
    ON i.project_id = p.id

LEFT JOIN stock_logs s
    ON s.item_id = i.id
    AND s.project_id = p.id

WHERE
    i.project_id = ?
"""

params = [project_id]

if search_text:

    query += """
    AND (
        i.code LIKE ?
        OR i.name LIKE ?
    )
    """

    params.append(f"%{search_text}%")
    params.append(f"%{search_text}%")

query += """

GROUP BY
    i.id,
    p.code,
    p.name,
    i.code,
    i.name

ORDER BY
    i.code
"""

rows = conn.execute(
    query,
    params
).fetchall()

if not rows:
    st.info("棚卸対象の商品がありません")
    st.stop()

# =====================
# 編集用データ作成
# =====================

data = []

for row in rows:

    current_qty = row["current_qty"]

    data.append(
        {
            "商品ID": row["item_id"],
            "案件コード": row["project_code"],
            "案件名": row["project_name"],
            "商品コード": row["item_code"],
            "商品名": row["item_name"],
            "現在庫": current_qty,
            "実棚数": current_qty
        }
    )

df = pd.DataFrame(data)

st.info(
    "実際に数えた数を「実棚数」に入力してください。差異がある商品だけ棚卸調整として登録されます。"
)

edited_df = st.data_editor(
    df,
    use_container_width=True,
    hide_index=True,
    disabled=[
        "商品ID",
        "案件コード",
        "案件名",
        "商品コード",
        "商品名",
        "現在庫"
    ],
    column_config={
        "実棚数": st.column_config.NumberColumn(
            "実棚数",
            min_value=0,
            step=1
        )
    }
)

# =====================
# 差異計算
# =====================

edited_df["差異"] = (
    edited_df["実棚数"] - edited_df["現在庫"]
)

diff_df = edited_df[
    edited_df["差異"] != 0
].copy()
diff_df["登録対象"] = True

st.subheader("棚卸差異")

st.write(
    f"差異あり：{len(diff_df)}件"
)

edited_diff_df = st.data_editor(
    diff_df[
        [
            "登録対象",
            "商品ID",
            "商品コード",
            "商品名",
            "現在庫",
            "実棚数",
            "差異"
        ]
    ],
    use_container_width=True,
    hide_index=True,
    disabled=[
        "商品ID",
        "商品コード",
        "商品名",
        "現在庫",
        "実棚数",
        "差異"
    ]
)

selected_diff_df = edited_diff_df[
    edited_diff_df["登録対象"] == True
].copy()

st.write(
    f"登録対象：{len(selected_diff_df)}件"
)


# =====================
# 棚卸調整登録
# =====================

confirm = st.checkbox(
    "差異を棚卸調整として登録する"
)

if st.button(
    "棚卸差異を登録",
    disabled=selected_diff_df.empty
):

    if not confirm:

        st.warning(
            "登録する場合はチェックを入れてください"
        )

        st.stop()

    for _, row in selected_diff_df.iterrows():

        conn.execute(
            """
            INSERT INTO stock_logs(
                project_id,
                item_id,
                qty,
                type,
                username
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_id,
                int(row["商品ID"]),
                int(row["差異"]),
                "棚卸調整",
                st.session_state.username
            )
        )

    conn.commit()

    st.success(
    f"棚卸調整を登録しました：{len(selected_diff_df)}件"
)
    
    st.rerun()