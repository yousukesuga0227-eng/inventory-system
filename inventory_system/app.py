import streamlit as st
from database import get_connection
import os
import shutil
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)

logo_path = os.path.join(
    BASE_DIR,
    "logo.png"
)

conn = get_connection()

# -------------------
# ログイン
# -------------------

USERS = {
"admin": {
"password": "1234",
"role": "admin"
},

"user": {
    "password": "0000",
    "role": "user"
},

"壽賀": {
    "password": "0227",
    "role": "admin"
},

"若杉": {
    "password": "0000",
    "role": "admin"
},

"鮫島": {
    "password": "0904",
    "role": "admin"
},

"山縣": {
    "password": "1111",
    "role": "user"
},

"小寺": {
    "password": "2222",
    "role": "user"
},

"河野": {
    "password": "3333",
    "role": "user"
},

}

if "login" not in st.session_state:
    st.session_state.login = False

if "role" not in st.session_state:
    st.session_state.role = None

if not st.session_state.login:

    st.title("🔐 ログイン")

    username = st.text_input(
        "ユーザー名"
    )

    password = st.text_input(
        "パスワード",
        type="password"
    )

    if st.button("ログイン"):

        if username in USERS:

            if password == USERS[username]["password"]:

                st.session_state.login = True
                st.session_state.role = USERS[username]["role"]
                st.session_state.username = username

                st.rerun()

            else:

                st.error(
                    "パスワードが違います"
                )

        else:

            st.error(
                "ユーザーが存在しません"
            )

    st.stop()

st.set_page_config(
    page_title="大阪陸運 八尾倉庫 在庫管理システム",
    layout="wide"
)

st.markdown(
    """
    <h1 style='text-align: center;'>
        大阪陸運 八尾倉庫 在庫管理システム
    </h1>

    <p style='text-align: center; color: gray;'>
        Inventory Management System
    </p>
    """,
    unsafe_allow_html=True
)

st.write("---")

if st.button("💾 DBバックアップ"):

    db_path = os.path.join(
        BASE_DIR,
        "data",
        "inventory.db"
    )

    backup_name = (
        f"backup_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )

    shutil.copy(
        db_path,
        backup_name
    )

    st.success(
        f"バックアップ作成完了: {backup_name}"
    )

st.write("---")


# 件数取得
project_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM projects
    """
).fetchone()[0]

item_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM items
    """
).fetchone()[0]

log_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM stock_logs
    """
).fetchone()[0]

# 横並び
col1, col2, col3 = st.columns(3)

with col1:

    st.metric(
        "案件数",
        project_count
    )

with col2:

    st.metric(
        "商品数",
        item_count
    )

with col3:

    st.metric(
        "入出庫履歴",
        log_count
    )

st.write("---")

st.markdown("## 📋 システムメニュー")

col1, col2 = st.columns([8, 1])

with col2:

    if st.button("🚪ログアウト"):

        st.session_state.login = False

        st.session_state.role = None

        st.rerun()

col1, col2 = st.columns(2)

with col1:

    if st.session_state.role == "admin":

        st.page_link(
            "pages/01_projects.py",
            label="📁 案件管理"
        )

        st.page_link(
            "pages/02_items.py",
            label="📦 商品管理"
        )

    st.page_link(
        "pages/03_stock.py",
        label="📥📤 入出庫登録"
    )

with col2:

    st.page_link(
        "pages/04_stock_list.py",
        label="📊 在庫一覧"
    )

    st.page_link(
        "pages/05_history.py",
        label="📝 入出庫履歴"
    )

    st.page_link(
        "pages/07_item_search.py",
        label="🔍 商品検索"
    )

    st.page_link(
    "pages/08_shipping_instruction.py",
    label="📄 出荷指示書"
)

st.caption(
    "大阪陸運 八尾倉庫 在庫管理システム Ver 1.001"
)