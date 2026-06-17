import streamlit as st
from database import get_connection
import os
import shutil
import time
from datetime import datetime

st.set_page_config(
    page_title="大阪陸運 | SHARK",
    layout="wide"
)

BASE_DIR = os.path.dirname(__file__)

logo_path = os.path.join(BASE_DIR, "20260608-logo.png")
boot_logo_path = os.path.join(BASE_DIR, "assets", "shark_01.jpg")

if "login" not in st.session_state:
    st.session_state.login = False

if "role" not in st.session_state:
    st.session_state.role = None

if "username" not in st.session_state:
    st.session_state.username = None

if "display_name" not in st.session_state:
    st.session_state.display_name = None


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
        time.sleep(0.4)

    time.sleep(0.4)
    progress.empty()
    status.empty()

    if os.path.exists(boot_logo_path):
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image(boot_logo_path, use_container_width=True)

    time.sleep(2.5)

    st.markdown(
        """
        <div style="text-align:center;">
            <h2 style="color:#00cc66;">ACCESS GRANTED</h2>
        </div>
        """,
        unsafe_allow_html=True
    )

    time.sleep(0.8)
    st.session_state.shark_boot_done = True
    st.rerun()


# =====================
# ログイン画面
# =====================

if not st.session_state.login:

    st.title("🔐 ログイン")

    username = st.text_input("ユーザー名")
    password = st.text_input("パスワード", type="password")

    if st.button("ログイン"):

        conn = get_connection()

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE username = ?
            AND password = ?
            AND is_active = TRUE
        """, (
            username.lower().strip(),
            password.strip()
        )).fetchone()

        if user:

            st.session_state.login = True
            st.session_state.role = user["role"]
            st.session_state.username = user["username"]
            st.session_state.display_name = user["display_name"]

            conn.execute("""
                INSERT INTO login_logs (
                    user_id,
                    username,
                    display_name,
                    role
                )
                VALUES (?, ?, ?, ?)
            """, (
                user["id"],
                user["username"],
                user["display_name"],
                user["role"]
            ))

            conn.commit()
            conn.close()

            st.rerun()

        else:
            conn.close()
            st.error("ユーザー名またはパスワードが違います")

    st.stop()


# =====================
# ホーム画面
# =====================

def home_page():

    conn = get_connection()

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

    if os.path.exists(logo_path):

        header_left, header_right = st.columns([8, 1])

        with header_right:
            st.page_link(
                "pages/99_manual.py",
                label="❓ ヘルプ"
            )

        col1, col2, col3 = st.columns([1, 3, 1])

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

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("案件数", project_count)

    with col2:
        st.metric("商品数", item_count)

    with col3:
        st.metric("入出庫履歴", log_count)

    st.write("---")

    st.markdown(
        '<div class="menu-title">📋 システムメニュー</div>',
        unsafe_allow_html=True
    )

    logout_col1, logout_col2 = st.columns([8, 1])

    with logout_col2:

        if st.button("🚪ログアウト"):

            st.session_state.login = False
            st.session_state.role = None
            st.session_state.username = None
            st.session_state.display_name = None

            st.rerun()

    menu_items = []

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

    if st.session_state.role in ["admin", "user", "warehouse", "label_user"]:

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
                    "page": "pages/07_item_search.py",
                    "label": "🔍 商品検索"
                },
                {
                    "page": "pages/08_shipping_instruction.py",
                    "label": "📄 出荷指示書"
                },
                {
                    "page": "pages/11_label_print.py",
                    "label": "🏷️ ラベル出力"
                },
                
            ]
        )

    if st.session_state.role == "admin":

        menu_items.extend(
            [
                {
                    "page": "pages/05_history.py",
                    "label": "📝 入出庫履歴"
                },
                {
                    "page": "pages/06_inventory_check.py",
                    "label": "🧮 棚卸"
                },
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

    for i in range(0, len(menu_items), 3):

        cols = st.columns(3)

        row_items = menu_items[i:i + 3]

        for col, item in zip(cols, row_items):

            with col:

                st.page_link(
                    item["page"],
                    label=item["label"]
                )

    st.write("---")

    st.caption(
        "大阪陸運 八尾倉庫 在庫管理システム Ver 1.101"
    )

    conn.close()


# =====================
# サイドバー表示制御
# =====================

pages = [
    st.Page(home_page, title="ホーム", icon="🏠"),
    st.Page("pages/03_stock.py", title="入出庫登録", icon="📥"),
    st.Page("pages/04_stock_list.py", title="在庫一覧", icon="📊"),
    st.Page("pages/07_item_search.py", title="商品検索", icon="🔍"),
    st.Page("pages/08_shipping_instruction.py", title="出荷指示書", icon="📄"),
    st.Page("pages/11_label_print.py", title="ラベル出力", icon="🏷️"),
    st.Page("pages/99_manual.py", title="ヘルプ", icon="❓"),
]

if st.session_state.role == "admin":

    pages.extend(
        [
            st.Page("pages/01_projects.py", title="案件管理", icon="📁"),
            st.Page("pages/02_items.py", title="商品管理", icon="📦"),
            st.Page("pages/05_history.py", title="入出庫履歴", icon="📝"),
            st.Page("pages/06_inventory_check.py", title="棚卸", icon="🧮"),
            st.Page("pages/09_operation_logs.py", title="操作履歴", icon="🧾"),
            st.Page("pages/10_admin.py", title="管理ページ", icon="⚙️"),
        ]
    )

pg = st.navigation(pages)
pg.run()