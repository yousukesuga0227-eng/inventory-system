import streamlit as st
from database import get_connection, log_action
from auth import check_admin

check_admin()

conn = get_connection()

st.title("👤 ユーザー管理")
st.success(f"ログイン中：{st.session_state.username}")

tabs = st.tabs([
    "ユーザー一覧",
    "新規追加",
    "ユーザー編集"
])

# =====================
# ユーザー一覧
# =====================
with tabs[0]:

    st.subheader("ユーザー一覧")

    users = conn.execute("""
        SELECT
            id,
            username,
            display_name,
            role,
            is_active,
            created_at
        FROM users
        ORDER BY id
    """).fetchall()

    data = []

    for u in users:
        data.append({
            "ID": u["id"],
            "ログインID": u["username"],
            "表示名": u["display_name"],
            "権限": u["role"],
            "有効": "はい" if u["is_active"] else "いいえ",
            "作成日": u["created_at"],
        })

    st.dataframe(data, use_container_width=True, hide_index=True)

# =====================
# 新規追加
# =====================
with tabs[1]:

    st.subheader("新規ユーザー追加")

    new_username = st.text_input("ログインID")
    new_display_name = st.text_input("表示名")
    new_password = st.text_input("パスワード", type="password")

    new_role = st.selectbox(
        "権限",
        ["user", "admin"],
        key="new_user_role"
    )

    if st.button("ユーザーを追加"):

        clean_username = new_username.lower().strip()

        if not clean_username or not new_password.strip():
            st.warning("ログインIDとパスワードは必須です")
            st.stop()

        existing_user = conn.execute("""
            SELECT *
            FROM users
            WHERE username = ?
        """, (
            clean_username,
        )).fetchone()

        if existing_user:
            st.warning("このログインIDは既に使われています")
            st.stop()

        conn.execute("""
            INSERT INTO users (
                username,
                password,
                display_name,
                role,
                is_active,
                created_at
            )
            VALUES (?, ?, ?, ?, TRUE, CURRENT_TIMESTAMP)
        """, (
            clean_username,
            new_password.strip(),
            new_display_name.strip(),
            new_role
        ))

        conn.commit()

        log_action(
            st.session_state.username,
            "ユーザー追加",
            "users",
            None,
            clean_username,
            f"権限: {new_role}"
        )

        st.success("ユーザーを追加しました")
        st.rerun()

# =====================
# ユーザー編集
# =====================
with tabs[2]:

    st.subheader("ユーザー編集")

    users = conn.execute("""
        SELECT *
        FROM users
        ORDER BY id
    """).fetchall()

    if not users:

        st.info("ユーザーがありません")

    else:

        user_options = {
            f'{u["id"]} / {u["username"]} / {u["display_name"]}': u["id"]
            for u in users
        }

        selected_user_label = st.selectbox(
            "編集するユーザー",
            list(user_options.keys())
        )

        user_id = user_options[selected_user_label]

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()

        edit_display_name = st.text_input(
            "表示名",
            value=user["display_name"] or ""
        )

        role_options = ["user", "admin"]
        current_role = user["role"] or "user"

        if current_role not in role_options:
            current_role = "user"

        edit_role = st.selectbox(
            "権限",
            role_options,
            index=role_options.index(current_role),
            key="edit_user_role"
        )

        edit_is_active = st.checkbox(
            "有効",
            value=bool(user["is_active"])
        )

        new_password = st.text_input(
            "新しいパスワード（変更する場合のみ）",
            type="password"
        )

        if st.button("ユーザー情報を更新"):
            # 自分自身の無効化禁止
            if (
                user["username"] == st.session_state.username
                and not edit_is_active
            ):
                st.error("自分自身を無効化することはできません")
                st.stop()
                
            # 管理者0人化の防止
            if (
                user["role"] == "admin"
                and edit_role != "admin"
            ):
                admin_count = conn.execute("""
                    SELECT COUNT(*)
                    FROM users
                    WHERE role = 'admin'
                    AND is_active = TRUE
                """).fetchone()[0]

                if admin_count <= 1:
                    st.error("管理者を0人にすることはできません")
                    st.stop()   

            conn.execute("""
                UPDATE users
                SET
                    display_name = ?,
                    role = ?,
                    is_active = ?
                WHERE id = ?
            """, (
                edit_display_name.strip(),
                edit_role,
                edit_is_active,
                user_id
            ))

            if new_password.strip():
                conn.execute("""
                    UPDATE users
                    SET password = ?
                    WHERE id = ?
                """, (
                    new_password.strip(),
                    user_id
                ))

            conn.commit()

            log_action(
                st.session_state.username,
                "ユーザー編集",
                "users",
                user_id,
                user["username"],
                f"権限: {edit_role} / 有効: {edit_is_active}"
            )

            st.success("ユーザー情報を更新しました")
            st.rerun()