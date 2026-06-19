import streamlit as st
from datetime import datetime
from database import get_connection, log_action
from auth import check_admin

check_admin()

conn = get_connection()

st.title("⚙️ 管理ページ")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

tabs = st.tabs(
    [
        "案件編集",
        "商品編集",
        "非表示管理",
        "完了案件確認"
    ]
)

# =====================
# 案件編集
# =====================

with tabs[0]:

    st.subheader("案件編集")

    projects = conn.execute(
        """
        SELECT *
        FROM projects
        ORDER BY id DESC
        """
    ).fetchall()

    if not projects:

        st.info("案件がありません")

    else:

        project_options = {
            f'{p["id"]} / {p["code"]} / {p["name"]}': p["id"]
            for p in projects
        }

        selected_project_label = st.selectbox(
            "編集する案件",
            list(project_options.keys())
        )

        project_id = project_options[selected_project_label]

        project = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        new_code = st.text_input(
            "案件コード",
            value=project["code"] or ""
        )

        new_name = st.text_input(
            "案件名",
            value=project["name"] or ""
        )

        new_receive_date = st.text_input(
            "入庫予定日",
            value=project["receive_date"] or ""
        )

        new_shipping_date = st.text_input(
            "出荷予定日",
            value=project["shipping_date"] or ""
        )

        status_options = [
            "未着荷",
            "入庫済",
            "出荷待ち",
            "出荷済",
            "完了"
        ]

        current_status = project["status"] or "未着荷"

        if current_status not in status_options:
            status_options.append(current_status)

        new_status = st.selectbox(
            "案件状態",
            status_options,
            index=status_options.index(current_status)
        )

        new_memo = st.text_area(
            "備考",
            value=project["memo"] or ""
        )

        if st.button("案件情報を更新"):

            conn.execute(
                """
                UPDATE projects
                SET
                    code = ?,
                    name = ?,
                    receive_date = ?,
                    shipping_date = ?,
                    status = ?,
                    memo = ?
                WHERE id = ?
                """,
                (
                    new_code,
                    new_name,
                    new_receive_date,
                    new_shipping_date,
                    new_status,
                    new_memo,
                    project_id
                )
            )

            conn.commit()

            log_action(
                st.session_state.username,
                "案件編集",
                "projects",
                project_id,
                new_name,
                f"案件コード: {new_code}"
            )

            st.success("案件情報を更新しました")
            st.rerun()

# =====================
# 商品編集
# =====================

with tabs[1]:

    st.subheader("商品編集")

    projects = conn.execute(
        """
        SELECT *
        FROM projects
        ORDER BY name
        """
    ).fetchall()

    if not projects:

        st.info("案件がありません")

    else:

        project_options = {
            f'{p["code"]} / {p["name"]}': p["id"]
            for p in projects
        }

        selected_project_label = st.selectbox(
            "案件で絞り込み",
            list(project_options.keys()),
            key="item_project_filter"
        )

        selected_project_id = project_options[selected_project_label]

        items = conn.execute(
            """
            SELECT *
            FROM items
            WHERE project_id = ?
            ORDER BY code
            """,
            (selected_project_id,)
        ).fetchall()

        if not items:

            st.info("この案件には商品がありません")

        else:

            item_options = {
                f'{i["id"]} / {i["code"]} / {i["name"]}': i["id"]
                for i in items
            }

            selected_item_label = st.selectbox(
                "編集する商品",
                list(item_options.keys())
            )

            item_id = item_options[selected_item_label]

            item = conn.execute(
                """
                SELECT *
                FROM items
                WHERE id = ?
                """,
                (item_id,)
            ).fetchone()

            new_item_code = st.text_input(
                "商品コード",
                value=item["code"] or ""
            )

            new_item_name = st.text_input(
                "商品名",
                value=item["name"] or ""
            )
            
            new_required_quantity = st.number_input(
                "出荷数",
                min_value=1,
                value=int(item["required_quantity"])
            )

            if st.button("出荷数を更新", key=f"update_qty_{item['id']}"):

                conn.execute(
                    """
                    UPDATE items
                    SET required_quantity = ?
                    WHERE id = ?
                    """,
                    (
                        new_required_quantity,
                        item["id"]
                    )
                )

                conn.commit()
                st.success("出荷数を更新しました")
                st.rerun()

            if st.button("商品情報を更新"):

                conn.execute(
                    """
                    UPDATE items
                    SET
                        code = ?,
                        name = ?
                    WHERE id = ?
                    """,
                    (
                        new_item_code,
                        new_item_name,
                        item_id
                    )
                )

                conn.commit()

                log_action(
                    st.session_state.username,
                    "商品編集",
                    "items",
                    item_id,
                    new_item_name,
                    f"商品コード: {new_item_code}"
                )

                st.success("商品情報を更新しました")
                st.rerun()

# =====================
# 非表示管理
# =====================

with tabs[2]:

    st.subheader("🙈 非表示管理")

    st.warning(
        "チェックONで通常画面から非表示にします。チェックOFFで表示に戻します。"
    )

    projects = conn.execute(
        """
        SELECT *
        FROM projects
        WHERE COALESCE(is_hidden, FALSE) = FALSE
        ORDER BY name
        """
    ).fetchall()

    if not projects:
        st.info("案件がありません")

    else:
        project_options = {
            f'{p["code"]} / {p["name"]}': p["id"]
            for p in projects
        }

        selected_project_label = st.selectbox(
            "案件を選択",
            list(project_options.keys()),
            key="hide_manage_project"
        )

        selected_project_id = project_options[selected_project_label]

        items = conn.execute(
            """
            SELECT
                id,
                code,
                name,
                COALESCE(is_hidden, FALSE) AS is_hidden
            FROM items
            WHERE project_id = ?
            ORDER BY code
            """,
            (selected_project_id,)
        ).fetchall()

        if not items:
            st.info("この案件には商品がありません")

        else:
            st.write("### 案件内の商品一覧")

            for item in items:
                st.checkbox(
                    f'{item["code"]} / {item["name"]}',
                    value=bool(item["is_hidden"]),
                    key=f'hide_item_{item["id"]}'
                )

            if st.button("非表示設定を更新", type="primary"):

                for item in items:
                    new_hidden = st.session_state[f'hide_item_{item["id"]}']

                    conn.execute(
                        """
                        UPDATE items
                        SET is_hidden = ?
                        WHERE id = ?
                        """,
                        (
                            new_hidden,
                            item["id"]
                        )
                    )

                conn.commit()

                st.success("非表示設定を更新しました")
                st.rerun()

# =====================
# 完了案件確認
# =====================

with tabs[3]:

    st.subheader("完了案件確認")

    completed_projects = conn.execute(
        """
        SELECT *
        FROM projects
        WHERE status = ?
        ORDER BY shipping_date DESC
        """,
        ("完了",)
    ).fetchall()

    if not completed_projects:

        st.info("完了案件はありません")

    else:

        data = []

        for p in completed_projects:

            data.append(
                {
                    "ID": p["id"],
                    "案件コード": p["code"],
                    "案件名": p["name"],
                    "入庫予定": p["receive_date"],
                    "出荷予定": p["shipping_date"],
                    "状態": p["status"],
                    "非表示": "はい" if p["is_hidden"] else "いいえ"
                }
            )

        st.dataframe(
            data,
            use_container_width=True,
            hide_index=True
        )


# =====================
# 🕶 SHARK BLACK BOX
# =====================

st.markdown("""
<style>
div[data-testid="stButton"] button[kind="secondary"] {
    background: transparent;
    border: none;
    padding: 0;
    color: #888888;
    font-size: 12px;
    box-shadow: none;
}
div[data-testid="stButton"] button[kind="secondary"]:hover {
    color: #444444;
    background: transparent;
    border: none;
}
</style>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 10])

with col1:
    if st.button("Ver 1.101", key="black_box_version_button"):
        st.session_state.show_black_box = not st.session_state.get(
            "show_black_box",
            False
        )

if st.session_state.get("show_black_box", False):

    st.subheader("🕶 SHARK BLACK BOX")

    logs = conn.execute("""
        SELECT
            username,
            role,
            login_at
        FROM login_logs
        ORDER BY login_at DESC
        LIMIT 100
    """).fetchall()

    data = []

    for log in logs:
        data.append({
            "ユーザーID": log["username"],
            "権限": log["role"],
            "ログイン日時": log["login_at"],
        })

    st.dataframe(
        data,
        use_container_width=True,
        hide_index=True
    )