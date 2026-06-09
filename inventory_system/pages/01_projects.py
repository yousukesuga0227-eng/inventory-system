import streamlit as st
import os
import barcode
from datetime import datetime
from barcode.writer import ImageWriter
from database import get_connection, log_action
from auth import check_admin

check_admin()

conn = get_connection()

st.title("案件管理")
st.success(
    f"ログイン中：{st.session_state.username}"
)

# =====================
# 案件登録フォーム
# =====================

st.subheader("案件登録")

code = st.text_input(
    "案件コード"
)

name = st.text_input(
    "案件名"
)

# =====================
# 入庫予定日
# =====================

receive_unknown = st.checkbox(
    "入庫予定日を未定にする"
)

if receive_unknown:

    receive_date = "未定"

    st.info(
        "入庫予定日は「未定」として登録されます"
    )

else:

    receive_date_input = st.date_input(
        "入庫予定日"
    )

    receive_date = receive_date_input.strftime(
        "%Y-%m-%d"
    )

# =====================
# 出荷予定日
# =====================

shipping_unknown = st.checkbox(
    "出荷予定日を未定にする"
)

if shipping_unknown:

    shipping_date = "未定"

    st.info(
        "出荷予定日は「未定」として登録されます"
    )

else:

    shipping_date_input = st.date_input(
        "出荷予定日"
    )

    shipping_date = shipping_date_input.strftime(
        "%Y-%m-%d"
    )

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

memo = st.text_area(
    "備考"
)

if st.button("案件登録"):

    if not code or not name:

        st.warning(
            "案件コードと案件名を入力してください"
        )

    else:

        cursor = conn.execute(
            """
            INSERT INTO projects(
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
            """,
            (
                code,
                name,
                receive_date,
                shipping_date,
                status,
                memo,
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        )

        new_project_id = cursor.fetchone()[0]

        conn.commit()

        log_action(
            st.session_state.username,
            "案件登録",
            "projects",
            new_project_id,
            name,
            f"案件コード: {code}"
        )

        # バーコード保存フォルダ作成
        os.makedirs(
            "barcodes/projects",
            exist_ok=True
        )

        # Code128
        barcode_class = barcode.get_barcode_class(
            "code128"
        )

        barcode_obj = barcode_class(
            code,
            writer=ImageWriter()
        )

        # 保存
        barcode_obj.save(
            f"barcodes/projects/{code}"
        )

        st.success(
            "案件登録 + バーコード生成完了"
        )

        st.rerun()

st.write("---")

# =====================
# 並び順
# =====================

sort_option = st.selectbox(
    "並び順",
    [
        "ID順",
        "案件コード順",
        "案件名順"
    ]
)

order_by = "id"

if sort_option == "案件コード順":

    order_by = "code"

elif sort_option == "案件名順":

    order_by = "name"

# =====================
# 案件一覧
# =====================

st.subheader("案件一覧")

search_text = st.text_input(
    "案件検索"
)

query = """
SELECT *
FROM projects
WHERE
    COALESCE(is_hidden, FALSE) = FALSE
"""

params = []

# 検索条件
if search_text:

    query += """
    AND (
        code LIKE ?
        OR name LIKE ?
    )
    """

    params.append(
        f"%{search_text}%"
    )

    params.append(
        f"%{search_text}%"
    )

query += f"""
ORDER BY {order_by}
"""

rows = conn.execute(
    query,
    params
).fetchall()

project_list = []

for row in rows:

    project_list.append(
        {
            "ID": row["id"],
            "案件コード": row["code"],
            "案件名": row["name"],
            "入庫予定": row["receive_date"],
            "出荷予定": row["shipping_date"],
            "状態": row["status"]
        }
    )

st.dataframe(
    project_list,
    use_container_width=True,
    hide_index=True
)

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

    st.info(
        "入庫予定日・出荷予定日が未定の案件はありません"
    )

else:

    st.write(
        f"未定の日付がある案件：{len(unknown_projects)}件"
    )

    for row in unknown_projects:

        with st.expander(
            f'{row["code"]} / {row["name"]}'
        ):

            st.write(
                f'現在の入庫予定：{row["receive_date"]}'
            )

            st.write(
                f'現在の出荷予定：{row["shipping_date"]}'
            )

            col1, col2 = st.columns(2)

            # 入庫予定日が未定の場合だけ再設定
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

                        conn.execute(
                            """
                            UPDATE projects
                            SET receive_date = ?
                            WHERE id = ?
                            """,
                            (
                                new_receive_date.strftime(
                                    "%Y-%m-%d"
                                ),
                                row["id"]
                            )
                        )

                        conn.commit()

                        log_action(
                            st.session_state.username,
                            "入庫予定日更新",
                            "projects",
                            row["id"],
                            row["name"],
                            f"入庫予定日: 未定 → {new_receive_date.strftime('%Y-%m-%d')}"
                        )

                        st.success(
                            "入庫予定日を更新しました"
                        )

                        st.rerun()

                else:

                    st.info(
                        "入庫予定日は設定済みです"
                    )

            # 出荷予定日が未定の場合だけ再設定
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

                        conn.execute(
                            """
                            UPDATE projects
                            SET shipping_date = ?
                            WHERE id = ?
                            """,
                            (
                                new_shipping_date.strftime(
                                    "%Y-%m-%d"
                                ),
                                row["id"]
                            )
                        )

                        conn.commit()

                        log_action(
                            st.session_state.username,
                            "出荷予定日更新",
                            "projects",
                            row["id"],
                            row["name"],
                            f"出荷予定日: 未定 → {new_shipping_date.strftime('%Y-%m-%d')}"
                        )

                        st.success(
                            "出荷予定日を更新しました"
                        )

                        st.rerun()

                else:

                    st.info(
                        "出荷予定日は設定済みです"
                    )