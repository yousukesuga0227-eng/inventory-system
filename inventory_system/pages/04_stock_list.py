import streamlit as st
import pandas as pd
from database import get_connection
from auth import check_login

check_login()
conn = get_connection()

st.title("在庫一覧")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

# =====================
# フィルタ用データ取得
# =====================

projects = conn.execute(
    """
    SELECT DISTINCT name
    FROM projects
    WHERE
        COALESCE(is_hidden, FALSE) = FALSE
    ORDER BY name
    """
).fetchall()

project_names = ["すべて"] + [
    p["name"] for p in projects
]

# =====================
# フィルタ
# =====================

st.subheader("絞り込み")

col1, col2 = st.columns(2)

with col1:
    selected_project = st.selectbox(
        "案件フィルタ",
        project_names
    )

with col2:
    search_text = st.text_input(
        "商品コード / 商品名で検索"
    )

show_zero_stock = st.checkbox(
    "在庫0の商品も表示する"
)

# =====================
# 在庫一覧取得
# =====================

query = """
SELECT
    p.code AS project_code,
    p.name AS project_name,
    i.code AS item_code,
    i.name AS item_name,
    COALESCE(SUM(s.qty), 0) AS total_qty
FROM items i

LEFT JOIN projects p
    ON i.project_id = p.id

LEFT JOIN stock_logs s
    ON s.item_id = i.id
    AND s.project_id = p.id

WHERE
    COALESCE(p.is_hidden, FALSE) = FALSE
    AND COALESCE(i.is_active, TRUE) = TRUE
"""

params = []

if selected_project != "すべて":

    query += """
    AND p.name = ?
    """

    params.append(selected_project)

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
    p.code,
    p.name,
    i.code,
    i.name
"""

if not show_zero_stock:

    query += """
HAVING COALESCE(SUM(s.qty), 0) > 0
"""

query += """

ORDER BY
    p.code,
    i.code
"""

rows = conn.execute(
    query,
    params
).fetchall()

data = []

for row in rows:

    stock_qty = row["total_qty"]

    if stock_qty < 0:
        stock_status = "マイナス在庫"
    elif stock_qty == 0:
        stock_status = "在庫なし"
    else:
        stock_status = "在庫あり"

    data.append(
        {
            "案件コード": row["project_code"],
            "案件名": row["project_name"],
            "商品コード": row["item_code"],
            "商品名": row["item_name"],
            "在庫数": stock_qty,
            "状態": stock_status
        }
    )

df = pd.DataFrame(data)

# =====================
# 集計表示
# =====================

if df.empty:

    total_items = 0
    total_stock = 0
    zero_count = 0

else:

    total_items = len(df)
    total_stock = int(df["在庫数"].sum())
    zero_count = int((df["在庫数"] == 0).sum())

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "表示商品数",
        f"{total_items}件"
    )

with col2:
    st.metric(
        "総在庫数",
        f"{total_stock}"
    )

with col3:
    st.metric(
        "在庫0件数",
        f"{zero_count}件"
    )

# =====================
# 一覧表示
# =====================

st.dataframe(
    df,
    use_container_width=True,
    hide_index=True
)

# =====================
# CSVダウンロード
# =====================

csv = df.to_csv(
    index=False
).encode("cp932", errors="ignore")

st.download_button(
    label="在庫一覧CSVダウンロード",
    data=csv,
    file_name="stock_list.csv",
    mime="text/csv",
    disabled=df.empty
)

if df.empty:
    st.info("表示できる在庫がありません")