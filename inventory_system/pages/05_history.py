import streamlit as st
import pandas as pd
from database import get_connection
from auth import check_login

check_login()
st.set_page_config(
    layout="wide"
)
conn = get_connection()

st.title("入出庫履歴")

query = """
SELECT
    s.id,
    p.code AS project_code,
    p.name AS project_name,
    i.code AS item_code,
    i.name AS item_name,
    s.qty,
    s.type,
    s.created_at

FROM stock_logs s

LEFT JOIN projects p
ON s.project_id = p.id

LEFT JOIN items i
ON s.item_id = i.id

ORDER BY s.created_at DESC
"""

rows = conn.execute(query).fetchall()

data = []

for row in rows:

    data.append(
        {
            "ID": row["id"],
            "日時": row["created_at"],
            "案件": row["project_name"],
            "商品": row["item_name"],
            "数量": row["qty"],
           "種別": (
    "入庫"
    if row["type"] == "IN"
    else "出庫"
)
        }
    )

st.dataframe(
    data,
    use_container_width=True
)

# 履歴削除
st.subheader("履歴削除")

delete_id = st.number_input(
    "削除する履歴ID",
    min_value=1,
    step=1
)

confirm_delete = st.checkbox(
    "本当に削除する"
)

if st.button("履歴削除"):

    if not confirm_delete:

        st.warning(
            "チェックを入れてください"
        )

    else:

        check = conn.execute(
            """
            SELECT *
            FROM stock_logs
            WHERE id = ?
            """,
            (delete_id,)
        ).fetchone()

        if not check:

            st.warning(
                "IDが存在しません"
            )

        else:

            conn.execute(
                """
                DELETE FROM stock_logs
                WHERE id = ?
                """,
                (delete_id,)
            )

            conn.commit()

            st.success("削除完了")

            st.rerun()