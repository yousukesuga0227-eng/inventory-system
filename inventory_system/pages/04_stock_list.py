import streamlit as st
import pandas as pd
from database import get_connection

conn = get_connection()

st.title("在庫一覧")

# 案件一覧取得
projects = conn.execute(
    "SELECT DISTINCT name FROM projects"
).fetchall()

project_names = ["すべて"] + [
    p["name"] for p in projects
]

selected_project = st.selectbox(
    "案件フィルタ",
    project_names
)

query = """
SELECT
    p.code AS project_code,
    p.name AS project_name,
    i.code AS item_code,
    i.name AS item_name,
    
    SUM(
        CASE
            WHEN s.type = 'IN'
            THEN s.qty
            ELSE -s.qty
        END
    ) AS total_qty
FROM stock_logs s

LEFT JOIN projects p
ON s.project_id = p.id

LEFT JOIN items i
ON s.item_id = i.id

WHERE 1=1
"""

params = []

if selected_project != "すべて":

    query += """
    AND p.name = ?
    """

    params.append(selected_project)

query += """

GROUP BY
    p.code,
    p.name,
    i.code,
    i.name

HAVING SUM(
    CASE
        WHEN s.type = 'IN'
        THEN s.qty
        ELSE -s.qty
    END
) > 0

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

    data.append({
        "案件コード": row["project_code"],
        "案件名": row["project_name"],
        "商品コード": row["item_code"],
        "商品名": row["item_name"],
        "在庫数": row["total_qty"]
    })

df = pd.DataFrame(data)

st.dataframe(
    df,
    use_container_width=True
)
csv = df.to_csv(
    index=False
).encode("utf-8-sig")

st.download_button(
    label="CSVダウンロード",
    data=csv,
    file_name="stock_list.csv",
    mime="text/csv"
)