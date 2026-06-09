import streamlit as st
from database import get_connection
import os
import shutil
import time
from datetime import datetime

# =====================
# ページ設定
# =====================

st.set_page_config(
    page_title="大阪陸運 | SHARK",
    layout="wide"
)

# =====================
# パス設定
# =====================

BASE_DIR = os.path.dirname(__file__)

logo_path = os.path.join(
    BASE_DIR,
    "20260608-logo.png"
)

boot_logo_path = os.path.join(
    BASE_DIR,
    "assets",
    "shark_01.jpg"
)

# =====================
# ログインユーザー
# 後で users.py / usersテーブルに移行予定
# =====================

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
        "password": "8147",
        "role": "admin"
    },
    "鮫島": {
        "password": "0904",
        "role": "admin"
    },
    "山縣": {
        "password": "1111",
        "role": "admin"
    },
    "小寺": {
        "password": "2222",
        "role": "admin"
    },
    "河野": {
        "password": "3333",
        "role": "admin"
    },
    "鮫島昇汰": {
        "password": "0416",
        "role": "admin"
    },
    "竹中": {
        "password": "0522",
        "role": "admin"
    },
}

# =====================
# セッション初期化
# =====================

if "login" not in st.session_state:
    st.session_state.login = False

if "role" not in st.session_state:
    st.session_state.role = None

if "username" not in st.session_state:
    st.session_state.username = None

# =====================
# SHARK 起動演出
# =====================

if "shark_boot_done" not in st.session_state:
    st.session_state.shark_boot_done = False

if not st.session_state.shark_boot_done:

    st.markdown(
        """
        <div style="text-align:center; padding-top:40px;">
            <h1 style="font-size:48px;">SHARK SYSTEM</h1>
            <p style="font-size:20px; letter-spacing:2px;">
                Smart Handling All Resource Keeper
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    progress = st.progress(0)
    status = st.empty()

    boot_messages = [
        "Initializing...",
        "Loading Inventory...",
        "Connecting Database...",
        "Checking Permissions...",
        "Authorizing User..."
    ]

    for i, msg in enumerate(boot_messages):
        status.markdown(f"### {msg}")
        progress.progress(int((i + 1) / len(boot_messages) * 100))
        time.sleep(0.35)

    st.markdown(
        """
        <div style="text-align:center;">
            <h2 style="color:#00cc66;">ACCESS GRANTED</h2>
        </div>
        """,
        unsafe_allow_html=True
    )

    if os.path.exists(boot_logo_path):
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image(
                boot_logo_path,
                use_container_width=True
            )

    st.markdown(
        """
        <div style="text-align:center; font-size:12px; opacity:0.65;">
            Version 1.03 / Build 2026.06<br>
            Internal System - Authorized Personnel Only
        </div>
        """,
        unsafe_allow_html=True
    )

    time.sleep(1.5)

    st.session_state.shark_boot_done = True
    st.rerun()

# =====================
# ログイン画面
# =====================

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

# =====================
# ログイン後ここから
# =====================

conn = get_connection()

# =====================
# CSS
# =====================

st.markdown(
    """
    <style>
    div[data-testid="stPageLink"] a {
        border: 1px solid #dddddd;
        border-radius: 14px;
        padding: 14px 18px;
        margin: 8px 0;
        background-color: #ffffff;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
        text-align: center;
        font-weight: 700;
        font-size: 17px;
        transition: 0.2s ease;
        min-height: 56px;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    div[data-testid="stPageLink"] a:hover {
        background-color: #f5f7fa;
        border-color: #999999;
        transform: translateY(-2px);
    }

    .menu-title {
    font-size: 34px;
    font-weight: 800;
    margin-top: 20px;
    margin-bottom: 14px;
}

    .system-subtitle {
        text-align: center;
        color: #555555;
        font-size: 20px;
        font-weight: 600;
        letter-spacing: 1px;
        margin-top: 4px;
        margin-bottom: 20px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# =====================
# TOPタイトル
# =====================

if os.path.exists(logo_path):

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:

        st.image(
            logo_path,
            use_container_width=True
        )

else:

    st.markdown(
        """
        <h1 style='text-align: center; font-size: 52px; margin-top: 20px;'>
            大阪陸運
        </h1>
        """,
        unsafe_allow_html=True
    )

    st.warning(
        "ロゴ画像が見つかりません。inventory_system/20260608-logo.png を配置してください。"
    )

st.markdown(
    """
    <p class="system-subtitle">
        Smart Handling All Resource Keeper System
    </p>
    """,
    unsafe_allow_html=True
)

st.write("---")

# =====================
# DBバックアップ
# クラウド版ではSQLiteバックアップ不可なのでローカル時だけ表示
# =====================

database_url = os.environ.get("DATABASE_URL")

try:
    if "DATABASE_URL" in st.secrets:
        database_url = st.secrets["DATABASE_URL"]
except Exception:
    pass

if not database_url:

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

else:

    st.info(
        "クラウドDB接続中：データはSupabaseに保存されています。"
    )

st.write("---")

# =====================
# 件数取得
# =====================

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

# =====================
# 件数表示
# =====================

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

# =====================
# システムメニュー
# =====================

st.markdown(
    '<div class="menu-title">📋 システムメニュー</div>',
    unsafe_allow_html=True
)

# ログアウトボタン
logout_col1, logout_col2 = st.columns([8, 1])

with logout_col2:

    if st.button("🚪ログアウト"):

        st.session_state.login = False
        st.session_state.role = None
        st.session_state.username = None

        st.rerun()

# =====================
# メニュー定義
# =====================

menu_items = []

# 管理者だけ表示
if st.session_state.role == "admin":

    menu_items.extend(
        [
            {
                "page": "pages/01_projects.py",
                "label": "📁 案件管理"
            },
            {
                "page": "pages/02_items.py",
                "label": "📦 商品管理"
            },
        ]
    )

# 一般ユーザーも表示
menu_items.extend(
    [
        {
            "page": "pages/03_stock.py",
            "label": "📥📤 入出庫登録"
        },
        {
            "page": "pages/04_stock_list.py",
            "label": "📊 在庫一覧"
        },
        {
            "page": "pages/05_history.py",
            "label": "📝 入出庫履歴"
        },
        {
            "page": "pages/06_inventory_check.py",
            "label": "🧮 棚卸"
        },
        {
            "page": "pages/07_item_search.py",
            "label": "🔍 商品検索"
        },
        {
            "page": "pages/08_shipping_instruction.py",
            "label": "📄 出荷指示書"
        },
    ]
)

# 管理者だけ表示
if st.session_state.role == "admin":

    menu_items.extend(
        [
            {
                "page": "pages/09_operation_logs.py",
                "label": "🧾 操作履歴"
            },
            {
                "page": "pages/10_admin.py",
                "label": "⚙️ 管理ページ"
            },
        ]
    )

    
# =====================
# 3列で均等表示
# =====================

for i in range(0, len(menu_items), 3):

    cols = st.columns(3)

    row_items = menu_items[
        i:i + 3
    ]

    for col, item in zip(cols, row_items):

        with col:

            st.page_link(
                item["page"],
                label=item["label"]
            )

st.write("---")

st.caption(
    "大阪陸運 八尾倉庫 在庫管理システム Ver 1.03"
)