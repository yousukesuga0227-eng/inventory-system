import streamlit as st
from datetime import datetime, timedelta
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

    st.info(
        "案件や商品を削除せず、通常画面の選択候補からだけ非表示にできます。"
        "在庫・履歴・過去データとの紐づきは残ります。"
    )

    completed_visible_projects = conn.execute(
        """
        SELECT id, code, name
        FROM projects
        WHERE
            status = '完了'
            AND COALESCE(is_hidden, FALSE) = FALSE
        ORDER BY name
        """
    ).fetchall()

    st.write("### ✅ 完了案件をまとめて非表示")

    completed_count = len(completed_visible_projects)

    if completed_count == 0:
        st.success("現在、非表示にする完了案件はありません。")
    else:
        st.warning(
            f"現在、表示中の完了案件が {completed_count} 件あります。"
        )

        with st.expander(
            f"対象の完了案件を見る（{completed_count}件）",
            expanded=False,
        ):
            st.dataframe(
                [
                    {
                        "案件コード": project["code"],
                        "案件名": project["name"],
                    }
                    for project in completed_visible_projects
                ],
                use_container_width=True,
                hide_index=True,
            )

        bulk_hide_confirm = st.checkbox(
            f"{completed_count}件の完了案件をまとめて非表示にする",
            key="bulk_hide_completed_confirm",
        )

        if st.button(
            "🙈 完了案件をまとめて非表示",
            type="primary",
            use_container_width=True,
            disabled=not bulk_hide_confirm,
            key="bulk_hide_completed_projects",
        ):
            try:
                project_ids = [
                    int(project["id"])
                    for project in completed_visible_projects
                ]
                placeholders = ",".join("?" for _ in project_ids)

                conn.execute(
                    f"""
                    UPDATE projects
                    SET is_hidden = TRUE
                    WHERE
                        id IN ({placeholders})
                        AND status = '完了'
                        AND COALESCE(is_hidden, FALSE) = FALSE
                    """,
                    tuple(project_ids),
                )
                conn.commit()

                log_action(
                    st.session_state.username,
                    "完了案件一括非表示",
                    "projects",
                    None,
                    "完了案件一括非表示",
                    f"{completed_count}件を非表示",
                )

                st.success(
                    f"完了案件 {completed_count} 件を非表示にしました。"
                )
                st.rerun()

            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                st.error(
                    f"完了案件の一括非表示に失敗しました：{exc}"
                )

    st.divider()
    st.write("### 📁 案件の表示・非表示")

    projects = conn.execute(
        """
        SELECT
            id,
            code,
            name,
            status,
            COALESCE(is_hidden, FALSE) AS is_hidden
        FROM projects
        ORDER BY
            COALESCE(is_hidden, FALSE),
            name
        """
    ).fetchall()

    if not projects:
        st.info("案件がありません")

    else:
        project_options = {
            (
                f'{"🙈 " if p["is_hidden"] else ""}'
                f'{p["code"]} / {p["name"]} / {p["status"]}'
            ): p["id"]
            for p in projects
        }

        selected_project_label = st.selectbox(
            "案件を選択",
            list(project_options.keys()),
            key="hide_manage_project",
        )
        selected_project_id = project_options[selected_project_label]

        selected_project = conn.execute(
            """
            SELECT
                id,
                code,
                name,
                status,
                COALESCE(is_hidden, FALSE) AS is_hidden
            FROM projects
            WHERE id = ?
            LIMIT 1
            """,
            (selected_project_id,),
        ).fetchone()

        project_hidden = st.checkbox(
            "🙈 この案件を通常画面から非表示にする",
            value=bool(selected_project["is_hidden"]),
            key=f'hide_project_{selected_project_id}',
        )

        if st.button(
            "案件の表示設定を更新",
            type="primary",
            use_container_width=True,
            key="update_project_visibility",
        ):
            try:
                conn.execute(
                    """
                    UPDATE projects
                    SET is_hidden = ?
                    WHERE id = ?
                    """,
                    (
                        bool(project_hidden),
                        selected_project_id,
                    ),
                )
                conn.commit()

                log_action(
                    st.session_state.username,
                    "案件非表示" if project_hidden else "案件再表示",
                    "projects",
                    selected_project_id,
                    selected_project["name"],
                    (
                        "通常画面から非表示"
                        if project_hidden
                        else "通常画面へ再表示"
                    ),
                )

                st.success(
                    "案件を非表示にしました。"
                    if project_hidden
                    else "案件を再表示しました。"
                )
                st.rerun()

            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                st.error(
                    f"案件の表示設定更新に失敗しました：{exc}"
                )

        st.caption(
            "案件を非表示にしても、商品側の個別非表示設定は変更しません。"
        )

        st.divider()
        st.write("### 📦 案件内商品の表示・非表示")

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
            (selected_project_id,),
        ).fetchall()

        if not items:
            st.info("この案件には商品がありません")
        else:
            st.caption(
                "チェックONで通常画面から非表示、"
                "OFFで表示に戻します。"
            )

            for item in items:
                st.checkbox(
                    f'{item["code"]} / {item["name"]}',
                    value=bool(item["is_hidden"]),
                    key=f'hide_item_{item["id"]}',
                )

            if st.button(
                "商品の非表示設定を更新",
                use_container_width=True,
                key="update_item_visibility",
            ):
                try:
                    for item in items:
                        new_hidden = st.session_state[
                            f'hide_item_{item["id"]}'
                        ]
                        conn.execute(
                            """
                            UPDATE items
                            SET is_hidden = ?
                            WHERE id = ?
                            """,
                            (
                                new_hidden,
                                item["id"],
                            ),
                        )

                    conn.commit()

                    log_action(
                        st.session_state.username,
                        "商品非表示設定",
                        "items",
                        None,
                        selected_project["name"],
                        "案件内商品の非表示設定を更新",
                    )

                    st.success("商品の非表示設定を更新しました")
                    st.rerun()

                except Exception as exc:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    st.error(
                        f"商品の非表示設定更新に失敗しました：{exc}"
                    )


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

# FIX: SHARK BLACK BOX scope 20260808
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
        dt = log["login_at"]

        if isinstance(dt, datetime):
            dt = dt + timedelta(hours=9)
            dt = dt.strftime("%Y/%m/%d %H:%M:%S")

        data.append({
            "ユーザーID": log["username"],
            "権限": log["role"],
            "ログイン日時": dt,
        })

    if data:
        st.dataframe(
            data,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("ログイン履歴はまだありません。")
