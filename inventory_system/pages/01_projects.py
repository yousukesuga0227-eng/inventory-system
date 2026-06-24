import streamlit as st
from datetime import datetime
from database import get_connection, log_action
from auth import check_admin

check_admin()

conn = get_connection()

st.title("案件管理")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

# =====================
# 案件登録フォーム
# =====================
st.subheader("案件登録")

# ---- 企業検索・選択 ----
search_company = st.text_input("企業検索（企業コード・企業名）")

companies = conn.execute("""
    SELECT id, code, name
    FROM companies
    WHERE COALESCE(is_active, TRUE) = TRUE
      AND (
        code ILIKE ?
        OR name ILIKE ?
      )
    ORDER BY code
    LIMIT 50
""", (
    f"%{search_company}%",
    f"%{search_company}%"
)).fetchall()

companies = [dict(row) for row in companies]

company_options = {
    f"{row['code']}：{row['name']}": row["id"]
    for row in companies
}

selected_company_id = None

if company_options:
    selected_company_label = st.selectbox(
        "企業",
        list(company_options.keys())
    )
    selected_company_id = company_options[selected_company_label]
else:
    st.warning("該当する企業がありません。企業管理から登録してください。")

# ---- 案件情報入力 ----
code = st.text_input("案件コード")
name = st.text_input("案件名")

receive_unknown = st.checkbox("入庫予定日を未定にする")

if receive_unknown:
    receive_date = "未定"
    st.info("入庫予定日は「未定」として登録されます")
else:
    receive_date = st.date_input("入庫予定日").strftime("%Y-%m-%d")

shipping_unknown = st.checkbox("出荷予定日を未定にする")

if shipping_unknown:
    shipping_date = "未定"
    st.info("出荷予定日は「未定」として登録されます")
else:
    shipping_date = st.date_input("出荷予定日").strftime("%Y-%m-%d")

status = st.selectbox(
    "案件状態",
    [
        "未着荷",
        "入庫済",
        "出荷待ち",
        "出荷済",
        "完了"
    ]
)

memo = st.text_area("備考")

# ---- 案件登録処理 ----
if st.button("案件登録"):

    if selected_company_id is None:
        st.warning("企業を選択してください。")

    elif not code or not name:
        st.warning("案件コードと案件名を入力してください。")

    else:
        cursor = conn.execute("""
            INSERT INTO projects (
                code,
                name,
                receive_date,
                shipping_date,
                status,
                memo,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, (
            code,
            name,
            receive_date,
            shipping_date,
            status,
            memo,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        new_project = cursor.fetchone()
        new_project_id = new_project["id"]

        conn.execute("""
            INSERT INTO project_companies (
                project_id,
                company_id
            )
            VALUES (?, ?)
            ON CONFLICT (project_id)
            DO UPDATE SET company_id = EXCLUDED.company_id
        """, (
            new_project_id,
            selected_company_id
        ))

        conn.commit()

        log_action(
            st.session_state.username,
            "案件登録",
            "projects",
            new_project_id,
            name,
            f"案件コード: {code}"
        )

        st.success("案件を登録しました。")
        st.rerun()

st.write("---")

# =====================
# 案件一覧
# =====================
st.subheader("案件一覧")

search_text = st.text_input("案件検索")

sort_option = st.selectbox(
    "並び順",
    [
        "ID順",
        "企業コード順",
        "案件コード順",
        "案件名順"
    ]
)

order_by = "p.id"

if sort_option == "企業コード順":
    order_by = "c.code"
elif sort_option == "案件コード順":
    order_by = "p.code"
elif sort_option == "案件名順":
    order_by = "p.name"

query = """
SELECT
    p.id,
    c.code AS company_code,
    c.name AS company_name,
    p.code,
    p.name,
    p.receive_date,
    p.shipping_date,
    p.status
FROM projects p
LEFT JOIN project_companies pc
    ON p.id = pc.project_id
LEFT JOIN companies c
    ON pc.company_id = c.id
WHERE COALESCE(p.is_hidden, FALSE) = FALSE
"""

params = []

if search_text:
    query += """
    AND (
        p.code ILIKE ?
        OR p.name ILIKE ?
        OR c.code ILIKE ?
        OR c.name ILIKE ?
    )
    """

    params.extend([
        f"%{search_text}%",
        f"%{search_text}%",
        f"%{search_text}%",
        f"%{search_text}%"
    ])

query += f"""
ORDER BY {order_by}
"""

rows = conn.execute(query, params).fetchall()
rows = [dict(row) for row in rows]

project_list = []

for row in rows:
    project_list.append({
        "ID": row["id"],
        "企業コード": row["company_code"],
        "企業名": row["company_name"],
        "案件コード": row["code"],
        "案件名": row["name"],
        "入庫予定": row["receive_date"],
        "出荷予定": row["shipping_date"],
        "状態": row["status"]
    })

STATUS_OPTIONS = [
    "未着荷",
    "入庫済",
    "出荷待ち",
    "出荷済",
    "完了"
]

edited_projects = st.data_editor(
    project_list,
    width="stretch",
    hide_index=True,
    disabled=[
        "ID",
        "企業コード",
        "企業名",
        "案件コード",
        "案件名",
        "入庫予定",
        "出荷予定",
    ],
    column_config={
        "状態": st.column_config.SelectboxColumn(
            "状態",
            options=STATUS_OPTIONS,
            required=True
        )
    },
    key="project_status_editor"
)

if st.button("変更したステータスを保存"):

    changed_count = 0

    original_map = {
        row["id"]: row["status"]
        for row in rows
    }

    for edited_row in edited_projects:

        project_id = edited_row["ID"]
        new_status = edited_row["状態"]
        old_status = original_map.get(project_id)

        if new_status != old_status:

            conn.execute("""
                UPDATE projects
                SET status = ?
                WHERE id = ?
            """, (
                new_status,
                project_id
            ))

            log_action(
                st.session_state.username,
                "案件ステータス変更",
                "projects",
                project_id,
                edited_row["案件名"],
                f"状態: {old_status} → {new_status}"
            )

            changed_count += 1

    conn.commit()

    if changed_count > 0:
        st.success(f"{changed_count}件のステータスを更新しました")
        st.rerun()
    else:
        st.info("変更はありません")


st.write("---")

# =====================
# 未定日付の再設定
# =====================
st.subheader("未定日付の再設定")

unknown_projects = [
    row
    for row in rows
    if row["receive_date"] == "未定"
    or row["shipping_date"] == "未定"
]

if not unknown_projects:
    st.info("入庫予定日・出荷予定日が未定の案件はありません")

else:
    st.write(f"未定の日付がある案件：{len(unknown_projects)}件")

    for row in unknown_projects:

        with st.expander(f'{row["code"]} / {row["name"]}'):

            st.write(f'現在の入庫予定：{row["receive_date"]}')
            st.write(f'現在の出荷予定：{row["shipping_date"]}')

            col1, col2 = st.columns(2)

            with col1:
                if row["receive_date"] == "未定":

                    new_receive_date = st.date_input(
                        "入庫予定日を設定",
                        key=f"receive_date_{row['id']}"
                    )

                    if st.button(
                        "入庫予定日を更新",
                        key=f"update_receive_{row['id']}"
                    ):
                        conn.execute("""
                            UPDATE projects
                            SET receive_date = ?
                            WHERE id = ?
                        """, (
                            new_receive_date.strftime("%Y-%m-%d"),
                            row["id"]
                        ))

                        conn.commit()

                        log_action(
                            st.session_state.username,
                            "入庫予定日更新",
                            "projects",
                            row["id"],
                            row["name"],
                            f"入庫予定日: 未定 → {new_receive_date.strftime('%Y-%m-%d')}"
                        )

                        st.success("入庫予定日を更新しました")
                        st.rerun()
                else:
                    st.info("入庫予定日は設定済みです")

            with col2:
                if row["shipping_date"] == "未定":

                    new_shipping_date = st.date_input(
                        "出荷予定日を設定",
                        key=f"shipping_date_{row['id']}"
                    )

                    if st.button(
                        "出荷予定日を更新",
                        key=f"update_shipping_{row['id']}"
                    ):
                        conn.execute("""
                            UPDATE projects
                            SET shipping_date = ?
                            WHERE id = ?
                        """, (
                            new_shipping_date.strftime("%Y-%m-%d"),
                            row["id"]
                        ))

                        conn.commit()

                        log_action(
                            st.session_state.username,
                            "出荷予定日更新",
                            "projects",
                            row["id"],
                            row["name"],
                            f"出荷予定日: 未定 → {new_shipping_date.strftime('%Y-%m-%d')}"
                        )

                        st.success("出荷予定日を更新しました")
                        st.rerun()
                else:
                    st.info("出荷予定日は設定済みです")

conn.close()