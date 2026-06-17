import streamlit as st
from database import get_connection
from auth import check_admin

check_admin()

conn = get_connection()

st.title("🧾 操作履歴")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

rows = conn.execute(
    """
    SELECT
        created_at,
        username,
        action,
        target_type,
        target_name,
        detail
    FROM operation_logs
    ORDER BY created_at DESC
    LIMIT 200
    """
).fetchall()

log_list = []

for row in rows:

    log_list.append(
        {
            "日時": row["created_at"],
            "ユーザー": row["username"],
            "操作": row["action"],
            "対象": row["target_type"],
            "名称": row["target_name"],
            "詳細": row["detail"]
        }
    )

st.dataframe(
    log_list,
    width="stretch"
)