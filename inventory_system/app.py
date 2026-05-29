import streamlit as st
from database import get_connection
import os

BASE_DIR = os.path.dirname(__file__)

logo_path = os.path.join(
    BASE_DIR,
    "logo.png"
)

conn = get_connection()

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

st.subheader("システムメニュー")

st.write("""
・案件管理  
・商品管理  
・入出庫登録  
・在庫一覧  
・履歴確認  
・商品検索  
""")

st.write("---")

st.caption(
    "大阪陸運 八尾倉庫 在庫管理システム Ver 1.0"
)