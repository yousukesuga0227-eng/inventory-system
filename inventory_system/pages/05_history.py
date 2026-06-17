import streamlit as st
import pandas as pd
from database import get_connection
from auth import check_login

st.set_page_config(
    layout="wide"
)

check_login()
conn = get_connection()

st.title("入出庫履歴")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

# =====================
# フィルタ用データ取得
# =====================

projects = conn.execute(
    """
    SELECT *
    FROM projects
    ORDER BY name
    """
).fetchall()

project_options = ["すべて"] + [
    p["name"] for p in projects
]

users = conn.execute(
    """
    SELECT DISTINCT username
    FROM stock_logs
    WHERE username IS NOT NULL
      AND username != ''
    ORDER BY username
    """
).fetchall()

user_options = ["すべて"] + [
    u["username"] for u in users
]

# =====================
# フィルタUI
# =====================

st.subheader("絞り込み")

col1, col2, col3 = st.columns(3)

with col1:
    selected_project = st.selectbox(
        "案件",
        project_options
    )

with col2:
    selected_type = st.selectbox(
        "種別",
        ["すべて", "入庫", "出庫"]
    )

with col3:
    selected_user = st.selectbox(
        "登録者",
        user_options
    )

search_text = st.text_input(
    "商品コード / 商品名 / 案件名で検索"
)

use_date_filter = st.checkbox(
    "日付で絞り込む"
)

if use_date_filter:

    col4, col5 = st.columns(2)

    with col4:
        start_date = st.date_input(
            "開始日"
        )

    with col5:
        end_date = st.date_input(
            "終了日"
        )

# =====================
# 履歴取得SQL
# =====================

query = """
SELECT
    s.id,
    p.code AS project_code,
    p.name AS project_name,
    i.code AS item_code,
    i.name AS item_name,
    s.qty,
    s.type,
    s.username,
    s.created_at
FROM stock_logs s

LEFT JOIN projects p
    ON s.project_id = p.id

LEFT JOIN items i
    ON s.item_id = i.id

WHERE 1=1
"""

params = []

# 案件フィルタ
if selected_project != "すべて":

    query += """
    AND p.name = ?
    """

    params.append(selected_project)

# 種別フィルタ
if selected_type == "入庫":

    query += """
    AND (
        s.type IN ('入庫', 'IN')
        OR s.qty > 0
    )
    """

elif selected_type == "出庫":

    query += """
    AND (
        s.type IN ('出庫', 'OUT')
        OR s.qty < 0
    )
    """

# 登録者フィルタ
if selected_user != "すべて":

    query += """
    AND s.username = ?
    """

    params.append(selected_user)

# 商品検索
if search_text:

    query += """
    AND (
        i.code LIKE ?
        OR i.name LIKE ?
        OR p.name LIKE ?
        OR p.code LIKE ?
    )
    """

    params.append(f"%{search_text}%")
    params.append(f"%{search_text}%")
    params.append(f"%{search_text}%")
    params.append(f"%{search_text}%")

# 日付フィルタ
if use_date_filter:

    query += """
    AND date(s.created_at) BETWEEN ? AND ?
    """

    params.append(start_date.strftime("%Y-%m-%d"))
    params.append(end_date.strftime("%Y-%m-%d"))

query += """
ORDER BY s.created_at DESC
"""

rows = conn.execute(
    query,
    params
).fetchall()

# =====================
# 表示データ作成
# =====================

data = []

for row in rows:

    # 種別表示
    if row["type"] in ["入庫", "IN"]:
        type_text = "入庫"
    elif row["type"] in ["出庫", "OUT"]:
        type_text = "出庫"
    else:
        type_text = row["type"]

    data.append(
        {
            "ID": row["id"],
            "日時": row["created_at"],
            "案件コード": row["project_code"],
            "案件名": row["project_name"],
            "商品コード": row["item_code"],
            "商品名": row["item_name"],
            "種別": type_text,
            "数量": abs(row["qty"]),
            "登録者": row["username"] if row["username"] else "不明"
        }
    )

df = pd.DataFrame(data)

st.write(
    f"表示件数：{len(df)}件"
)

st.dataframe(
    df,
    use_container_width=True
)

# =====================
# CSVダウンロード
# =====================

csv = df.to_csv(
    index=False
).encode("cp932", errors="ignore")

st.download_button(
    label="履歴CSVダウンロード",
    data=csv,
    file_name="stock_history.csv",
    mime="text/csv",
    disabled=df.empty
)

if df.empty:
    st.info("該当する履歴がありません")

# =====================
# 履歴削除
# =====================

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