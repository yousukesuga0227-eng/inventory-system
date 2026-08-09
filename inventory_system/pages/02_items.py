from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import streamlit as st

from auth import check_admin
from database import get_connection, log_action
from item_code_qr import (
    ItemCodeError,
    build_item_qr_payload,
    ensure_item_code_schema,
    get_item_category_by_code,
    get_project_company,
    is_postgres,
    list_item_categories,
    normalize_code_part,
    normalize_year_month,
    parse_item_code,
    peek_next_item_code,
    reserve_next_item_code,
    upsert_item_category,
)


JST = timezone(timedelta(hours=9), name="JST")

check_admin()
conn = get_connection()
ensure_item_code_schema(conn)


def _ensure_company_contact_column():
    if is_postgres(conn):
        conn.execute(
            "ALTER TABLE companies ADD COLUMN IF NOT EXISTS contact_person TEXT"
        )
    else:
        columns = conn.execute("PRAGMA table_info(companies)").fetchall()
        column_names = {row["name"] for row in columns}
        if "contact_person" not in column_names:
            conn.execute("ALTER TABLE companies ADD COLUMN contact_person TEXT")
    conn.commit()


_ensure_company_contact_column()

st.title("商品管理")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)


def _row_value(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        value = default
    return default if value is None else value


def _cursor_lastrowid(cursor):
    for candidate in (
        cursor,
        getattr(cursor, "cursor", None),
        getattr(cursor, "_cursor", None),
    ):
        if candidate is None:
            continue
        value = getattr(candidate, "lastrowid", None)
        if value is not None:
            return int(value)
    return None


def _insert_item(
    *,
    item_code,
    item_name,
    project_id,
    required_quantity,
    category_code,
    category_name,
    year_month,
    qr_payload,
):
    params = (
        item_code,
        item_name,
        project_id,
        required_quantity,
        category_code,
        category_name,
        year_month,
        qr_payload,
    )
    insert_sql = """
        INSERT INTO items (
            code,
            name,
            project_id,
            required_quantity,
            major_category_code,
            major_category_name,
            registered_year_month,
            qr_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    if is_postgres(conn):
        row = conn.execute(
            insert_sql.rstrip() + " RETURNING id",
            params,
        ).fetchone()
        try:
            return int(row["id"])
        except (KeyError, IndexError, TypeError):
            return int(row[0])

    cursor = conn.execute(insert_sql, params)
    new_id = _cursor_lastrowid(cursor)
    if new_id is None:
        raise ItemCodeError("登録した商品IDを取得できませんでした。")
    return new_id


def _item_exists(item_code):
    return conn.execute(
        "SELECT id FROM items WHERE code = ? LIMIT 1",
        (item_code,),
    ).fetchone() is not None


def _build_payload_for_manual_code(
    *,
    item_code,
    company,
    category,
    item_name,
    required_quantity,
):
    parsed = parse_item_code(item_code) or {}
    return build_item_qr_payload(
        item_code=item_code,
        company_code=company["company_code"],
        category_code=category["category_code"],
        year_month=parsed.get("year_month", normalize_year_month()),
        sequence=parsed.get("sequence", 0),
        project_code=company.get("project_code", ""),
        project_id=company.get("project_id", ""),
        item_name=item_name,
        required_quantity=required_quantity,
    )


# ============================================================
# 案件・商品を一本道で登録
# ============================================================
st.subheader("1．案件を選ぶ・登録する")
st.caption("既存案件を選ぶか、この画面内で新しい案件を登録してください。登録後はその案件が自動選択されます。")


def _load_companies(search_text=""):
    search_text = search_text.strip()
    like_text = f"%{search_text}%"

    rows = conn.execute(
        """
        SELECT
            id,
            code,
            name,
            COALESCE(contact_person, '') AS contact_person
        FROM companies
        WHERE COALESCE(is_active, TRUE) = TRUE
          AND (
              ? = ''
              OR LOWER(code) LIKE LOWER(?)
              OR LOWER(name) LIKE LOWER(?)
              OR LOWER(COALESCE(contact_person, '')) LIKE LOWER(?)
          )
        ORDER BY code
        """,
        (search_text, like_text, like_text, like_text),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_projects():
    return conn.execute(
        """
        SELECT
            p.id, p.code, p.name, p.receive_date, p.shipping_date,
            p.status, p.memo,
            c.id AS company_id, c.code AS company_code, c.name AS company_name
        FROM projects p
        LEFT JOIN project_companies pc ON p.id = pc.project_id
        LEFT JOIN companies c ON pc.company_id = c.id
        WHERE COALESCE(p.is_hidden, FALSE) = FALSE
        ORDER BY c.code, p.name
        """
    ).fetchall()


def _project_label(project):
    return (
        f"{_row_value(project, 'company_code', '荷主未設定')}："
        f"{_row_value(project, 'code', '')} / {_row_value(project, 'name', '')}"
    )


def _project_index(project_rows, selected_id):
    if not project_rows:
        return 0
    ids = [int(row["id"]) for row in project_rows]
    try:
        return ids.index(int(selected_id))
    except (ValueError, TypeError):
        return 0


def _date_text(value):
    return "" if value is None else str(value)


PROJECT_STATUS_OPTIONS = ["未着荷", "入庫済", "出荷待ち", "出荷済", "完了"]

if "selected_project_id" not in st.session_state:
    st.session_state.selected_project_id = None

project_tab, project_create_tab = st.tabs(["既存案件から選択", "＋ 新しい案件を登録"])
projects = _load_projects()
project_id = None

with project_create_tab:
    st.write("#### 新しい案件を登録")
    company_search = st.text_input(
        "企業検索（企業コード・企業名・担当者名）",
        placeholder="例：NTR / ニトリ / 田中",
        key="integrated_project_company_search",
    )
    companies = _load_companies(company_search)

    company_options = {}
    for row in companies:
        contact_person = str(row.get("contact_person") or "").strip()
        contact_label = f" ｜ 担当：{contact_person}" if contact_person else ""
        company_options[
            f"{row['code']}：{row['name']}{contact_label}"
        ] = int(row["id"])

    selected_company_id = None
    if company_options:
        selected_company_label = st.selectbox("企業", list(company_options.keys()), key="integrated_new_project_company")
        selected_company_id = company_options[selected_company_label]
    else:
        st.warning("該当する企業がありません。先に企業管理で登録してください。")

    new_col1, new_col2 = st.columns(2)
    with new_col1:
        new_project_code = st.text_input("案件コード", key="integrated_new_project_code")
        receive_unknown = st.checkbox("入庫予定日を未定にする", key="integrated_new_receive_unknown")
        if receive_unknown:
            new_receive_date = "未定"
            st.info("入庫予定日は「未定」で登録されます。")
        else:
            new_receive_date = st.date_input("入庫予定日", key="integrated_new_receive_date").strftime("%Y-%m-%d")

    with new_col2:
        new_project_name = st.text_input("案件名", key="integrated_new_project_name")
        shipping_unknown = st.checkbox("出荷予定日を未定にする", key="integrated_new_shipping_unknown")
        if shipping_unknown:
            new_shipping_date = "未定"
            st.info("出荷予定日は「未定」で登録されます。")
        else:
            new_shipping_date = st.date_input("出荷予定日", key="integrated_new_shipping_date").strftime("%Y-%m-%d")

    new_project_status = st.selectbox("案件状態", PROJECT_STATUS_OPTIONS, key="integrated_new_project_status")
    new_project_memo = st.text_area("備考", key="integrated_new_project_memo")

    if st.button("💾 案件を登録して商品入力へ進む", type="primary", use_container_width=True, key="integrated_create_project"):
        code_value = new_project_code.strip()
        name_value = new_project_name.strip()
        if selected_company_id is None:
            st.warning("企業を選択してください。")
        elif not code_value or not name_value:
            st.warning("案件コードと案件名を入力してください。")
        else:
            duplicate = conn.execute("SELECT id FROM projects WHERE code = ? LIMIT 1", (code_value,)).fetchone()
            if duplicate is not None:
                st.error("同じ案件コードがすでに登録されています。")
            else:
                try:
                    new_project_row = conn.execute(
                        """
                        INSERT INTO projects (
                            code, name, receive_date, shipping_date, status, memo, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        RETURNING id
                        """,
                        (
                            code_value, name_value, new_receive_date, new_shipping_date,
                            new_project_status, new_project_memo.strip(),
                            datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    ).fetchone()
                    try:
                        new_project_id = int(new_project_row["id"])
                    except (KeyError, IndexError, TypeError):
                        new_project_id = int(new_project_row[0])
                    conn.execute(
                        """
                        INSERT INTO project_companies (project_id, company_id)
                        VALUES (?, ?)
                        ON CONFLICT (project_id)
                        DO UPDATE SET company_id = EXCLUDED.company_id
                        """,
                        (new_project_id, selected_company_id),
                    )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    st.error(f"案件登録に失敗しました：{exc}")
                else:
                    log_action(
                        st.session_state.username, "案件登録", "projects",
                        new_project_id, name_value, f"案件コード: {code_value}",
                    )
                    st.session_state.selected_project_id = new_project_id
                    st.success("案件を登録しました。商品入力へ進みます。")
                    st.rerun()

with project_tab:
    if not projects:
        st.info("登録済み案件がありません。右のタブから新しい案件を登録してください。")
    else:
        project_labels = [_project_label(row) for row in projects]
        selected_index = _project_index(projects, st.session_state.selected_project_id)
        selected_project_label = st.selectbox(
            "案件選択", project_labels, index=selected_index, key="integrated_project_selector"
        )
        selected_project_row = projects[project_labels.index(selected_project_label)]
        project_id = int(selected_project_row["id"])
        st.session_state.selected_project_id = project_id
        st.caption(
            f"荷主：{_row_value(selected_project_row, 'company_code', '未設定')} / "
            f"{_row_value(selected_project_row, 'company_name', '')}"
        )

        with st.expander("✏️ 選択中の案件を編集", expanded=False):
            edit_company_search = st.text_input("企業検索", key=f"edit_company_search_{project_id}")
            edit_companies = _load_companies(edit_company_search)
            edit_company_options = {f"{row['code']}：{row['name']}": int(row["id"]) for row in edit_companies}
            current_company_id = _row_value(selected_project_row, "company_id", None)
            edit_company_labels = list(edit_company_options.keys())
            edit_company_index = 0
            if current_company_id is not None:
                for index, label in enumerate(edit_company_labels):
                    if edit_company_options[label] == int(current_company_id):
                        edit_company_index = index
                        break

            edited_company_id = current_company_id
            if edit_company_labels:
                edited_company_label = st.selectbox(
                    "企業", edit_company_labels, index=edit_company_index, key=f"edit_company_{project_id}"
                )
                edited_company_id = edit_company_options[edited_company_label]

            edit_col1, edit_col2 = st.columns(2)
            with edit_col1:
                edited_project_code = st.text_input(
                    "案件コード", value=str(_row_value(selected_project_row, "code", "")),
                    key=f"edit_project_code_{project_id}",
                )
                current_receive = _date_text(_row_value(selected_project_row, "receive_date", ""))
                edited_receive_unknown = st.checkbox(
                    "入庫予定日を未定にする", value=current_receive == "未定",
                    key=f"edit_receive_unknown_{project_id}",
                )
                if edited_receive_unknown:
                    edited_receive_date = "未定"
                else:
                    receive_default = datetime.now(JST).date()
                    if current_receive and current_receive != "未定":
                        try:
                            receive_default = datetime.strptime(current_receive[:10], "%Y-%m-%d").date()
                        except ValueError:
                            pass
                    edited_receive_date = st.date_input(
                        "入庫予定日", value=receive_default, key=f"edit_receive_date_{project_id}"
                    ).strftime("%Y-%m-%d")

            with edit_col2:
                edited_project_name = st.text_input(
                    "案件名", value=str(_row_value(selected_project_row, "name", "")),
                    key=f"edit_project_name_{project_id}",
                )
                current_shipping = _date_text(_row_value(selected_project_row, "shipping_date", ""))
                edited_shipping_unknown = st.checkbox(
                    "出荷予定日を未定にする", value=current_shipping == "未定",
                    key=f"edit_shipping_unknown_{project_id}",
                )
                if edited_shipping_unknown:
                    edited_shipping_date = "未定"
                else:
                    shipping_default = datetime.now(JST).date()
                    if current_shipping and current_shipping != "未定":
                        try:
                            shipping_default = datetime.strptime(current_shipping[:10], "%Y-%m-%d").date()
                        except ValueError:
                            pass
                    edited_shipping_date = st.date_input(
                        "出荷予定日", value=shipping_default, key=f"edit_shipping_date_{project_id}"
                    ).strftime("%Y-%m-%d")

            current_status = str(_row_value(selected_project_row, "status", "未着荷"))
            status_index = PROJECT_STATUS_OPTIONS.index(current_status) if current_status in PROJECT_STATUS_OPTIONS else 0
            edited_project_status = st.selectbox(
                "案件状態", PROJECT_STATUS_OPTIONS, index=status_index,
                key=f"edit_project_status_{project_id}",
            )
            edited_project_memo = st.text_area(
                "備考", value=str(_row_value(selected_project_row, "memo", "")),
                key=f"edit_project_memo_{project_id}",
            )

            if st.button("案件の変更を保存", use_container_width=True, key=f"save_project_edit_{project_id}"):
                code_value = edited_project_code.strip()
                name_value = edited_project_name.strip()
                if edited_company_id is None:
                    st.warning("企業を選択してください。")
                elif not code_value or not name_value:
                    st.warning("案件コードと案件名を入力してください。")
                else:
                    duplicate = conn.execute(
                        "SELECT id FROM projects WHERE code = ? AND id <> ? LIMIT 1",
                        (code_value, project_id),
                    ).fetchone()
                    if duplicate is not None:
                        st.error("同じ案件コードが別の案件で使用されています。")
                    else:
                        try:
                            conn.execute(
                                """
                                UPDATE projects
                                SET code = ?, name = ?, receive_date = ?, shipping_date = ?, status = ?, memo = ?
                                WHERE id = ?
                                """,
                                (
                                    code_value, name_value, edited_receive_date, edited_shipping_date,
                                    edited_project_status, edited_project_memo.strip(), project_id,
                                ),
                            )
                            conn.execute(
                                """
                                INSERT INTO project_companies (project_id, company_id)
                                VALUES (?, ?)
                                ON CONFLICT (project_id)
                                DO UPDATE SET company_id = EXCLUDED.company_id
                                """,
                                (project_id, edited_company_id),
                            )
                            conn.commit()
                        except Exception as exc:
                            conn.rollback()
                            st.error(f"案件更新に失敗しました：{exc}")
                        else:
                            log_action(
                                st.session_state.username, "案件更新", "projects",
                                project_id, name_value, f"案件コード: {code_value}",
                            )
                            st.success("案件情報を更新しました。")
                            st.rerun()

if project_id is None:
    st.info("商品を登録する案件を選択、または新規登録してください。")
    st.stop()

try:
    project_company = get_project_company(conn, project_id)
except ItemCodeError as exc:
    st.error(str(exc))
    st.info("この画面の案件編集から、案件に企業を設定してください。")
    st.stop()

st.divider()
st.subheader("2．大カテゴリーと商品を登録")
st.caption(
    f"選択中：{project_company.get('project_code', '')} / "
    f"{project_company.get('project_name', '')}"
)


# ============================================================
# 大カテゴリー管理
# ============================================================
with st.expander("⚙️ 大カテゴリーコード管理", expanded=False):
    st.write(
        "この荷主で使う大カテゴリーを登録します。"
        "同じコードを保存すると名称・有効状態を更新します。"
    )

    all_categories = list_item_categories(
        conn,
        project_company["company_id"],
        include_inactive=True,
    )

    col_code, col_name, col_active = st.columns([1, 2, 1])
    with col_code:
        master_category_code = st.text_input(
            "カテゴリーコード",
            placeholder="例：KAG",
            key=f"item_category_code_{project_company['company_id']}",
        )
    with col_name:
        master_category_name = st.text_input(
            "大カテゴリー名",
            placeholder="例：家具",
            key=f"item_category_name_{project_company['company_id']}",
        )
    with col_active:
        master_category_active = st.checkbox(
            "有効",
            value=True,
            key=f"item_category_active_{project_company['company_id']}",
        )

    if st.button(
        "大カテゴリーを保存",
        use_container_width=True,
        key=f"save_item_category_{project_company['company_id']}",
    ):
        try:
            saved = upsert_item_category(
                conn,
                project_company["company_id"],
                master_category_code,
                master_category_name,
                master_category_active,
            )
        except ItemCodeError as exc:
            st.error(str(exc))
        else:
            st.success(
                f"保存しました：{saved['category_code']} / "
                f"{saved['category_name']}"
            )
            st.rerun()

    if all_categories:
        category_master_rows = [
            {
                "コード": row["category_code"],
                "大カテゴリー": row["category_name"],
                "有効": bool(row["is_active"]),
            }
            for row in all_categories
        ]
        st.dataframe(
            category_master_rows,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("この荷主の大カテゴリーはまだ登録されていません。")

active_categories = list_item_categories(
    conn,
    project_company["company_id"],
    include_inactive=False,
)

if not active_categories:
    st.warning(
        "先に「大カテゴリーコード管理」でカテゴリーコードを登録してください。"
    )
    st.stop()

category_options = {
    f"{row['category_code']}：{row['category_name']}": int(row["id"])
    for row in active_categories
}

selected_category_label = st.selectbox(
    "大カテゴリー",
    list(category_options.keys()),
)
category_id = category_options[selected_category_label]
selected_category = next(
    row for row in active_categories if int(row["id"]) == category_id
)

registration_year_month = normalize_year_month(datetime.now(JST))

try:
    preview_code = peek_next_item_code(
        conn,
        project_id,
        category_id,
        registration_year_month,
    )
except ItemCodeError as exc:
    st.error(str(exc))
    st.stop()

st.info(f"次に発行される商品コード：{preview_code}")
st.caption(
    "形式：荷主コード-大カテゴリーコード-登録年月-4桁連番 / "
    "登録確定時にDBで正式採番します。"
)


# ============================================================
# 商品登録
# ============================================================
name = st.text_input("商品名", key="new_item_name")
required_quantity = st.number_input(
    "出荷数",
    min_value=1,
    value=1,
    step=1,
)

if name.strip():
    preview_parts = parse_item_code(preview_code) or {}
    preview_payload = build_item_qr_payload(
        item_code=preview_code,
        company_code=project_company["company_code"],
        category_code=selected_category["category_code"],
        year_month=registration_year_month,
        sequence=preview_parts.get("sequence", 1),
        project_code=project_company.get("project_code", ""),
        project_id=project_id,
        item_name=name.strip(),
        required_quantity=int(required_quantity),
    )
    with st.expander("QRへ入る情報を確認", expanded=False):
        st.code(preview_payload, language=None)
        st.caption(
            "QRを読んだときはSHARK側で商品コードだけを自動抽出します。"
        )

if st.button(
    "💾 商品登録",
    type="primary",
    use_container_width=True,
):
    item_name = name.strip()
    if not item_name:
        st.warning("商品名を入力してください。")
    else:
        try:
            (
                item_code,
                company,
                category,
                year_month,
                sequence,
            ) = reserve_next_item_code(
                conn,
                project_id,
                category_id,
                registration_year_month,
            )

            qr_payload = build_item_qr_payload(
                item_code=item_code,
                company_code=company["company_code"],
                category_code=category["category_code"],
                year_month=year_month,
                sequence=sequence,
                project_code=company.get("project_code", ""),
                project_id=project_id,
                item_name=item_name,
                required_quantity=int(required_quantity),
            )

            new_item_id = _insert_item(
                item_code=item_code,
                item_name=item_name,
                project_id=project_id,
                required_quantity=int(required_quantity),
                category_code=category["category_code"],
                category_name=category["category_name"],
                year_month=year_month,
                qr_payload=qr_payload,
            )
            conn.commit()
        except Exception as exc:
            st.error(f"商品登録に失敗しました：{exc}")
        else:
            log_action(
                st.session_state.username,
                "商品登録",
                "items",
                new_item_id,
                item_name,
                (
                    f"商品コード: {item_code} / 案件ID: {project_id} / "
                    f"大カテゴリー: {category['category_code']}"
                ),
            )
            st.success(f"商品登録完了：{item_code} / {item_name}")
            st.code(qr_payload, language=None)


# ============================================================
# CSV一括登録
# ============================================================
st.divider()
st.subheader("CSVで商品一括登録")
st.info(
    "CSV列：商品コード（任意）／大カテゴリーコード／商品名／出荷数。"
    "商品コードが空欄なら荷主＋カテゴリー＋登録年月＋4桁連番で自動採番します。"
)

sample_df = pd.DataFrame(
    [
        {
            "商品コード": "",
            "大カテゴリーコード": selected_category["category_code"],
            "商品名": "Falcon ダイニングチェア",
            "出荷数": 4,
        },
        {
            "商品コード": "",
            "大カテゴリーコード": selected_category["category_code"],
            "商品名": "Tsubomi テーブル",
            "出荷数": 1,
        },
    ]
)
sample_csv = sample_df.to_csv(index=False).encode("cp932", errors="ignore")

st.download_button(
    label="サンプルCSVをダウンロード",
    data=sample_csv,
    file_name="items_sample_auto_numbering.csv",
    mime="text/csv",
)

uploaded_file = st.file_uploader(
    "商品CSVをアップロード",
    type=["csv"],
)

if uploaded_file is not None:
    csv_bytes = uploaded_file.getvalue()

    try:
        df_upload = pd.read_csv(BytesIO(csv_bytes), encoding="cp932")
    except UnicodeDecodeError:
        df_upload = pd.read_csv(BytesIO(csv_bytes), encoding="utf-8-sig")

    df_upload.columns = [str(column).strip() for column in df_upload.columns]

    if "商品コード" not in df_upload.columns:
        df_upload["商品コード"] = ""
    if "出荷数" not in df_upload.columns:
        df_upload["出荷数"] = 1

    required_columns = ["大カテゴリーコード", "商品名"]
    missing_columns = [
        column for column in required_columns if column not in df_upload.columns
    ]

    if missing_columns:
        st.error("CSVに必要な列がありません：" + "、".join(missing_columns))
    else:
        df_upload = df_upload[
            ["商品コード", "大カテゴリーコード", "商品名", "出荷数"]
        ].fillna("")

        for column in ("商品コード", "大カテゴリーコード", "商品名"):
            df_upload[column] = df_upload[column].astype(str).str.strip()

        df_upload["大カテゴリーコード"] = (
            df_upload["大カテゴリーコード"].str.upper()
        )
        df_upload["出荷数"] = pd.to_numeric(
            df_upload["出荷数"],
            errors="coerce",
        ).fillna(1).astype(int)
        df_upload.loc[df_upload["出荷数"] < 1, "出荷数"] = 1
        df_upload = df_upload[df_upload["商品名"] != ""]

        st.write(f"読み込み件数：{len(df_upload)}件")
        st.dataframe(
            df_upload,
            use_container_width=True,
            hide_index=True,
        )

        if st.button(
            "CSVの商品を一括登録",
            use_container_width=True,
        ):
            created_items = []
            skipped_items = []
            error_items = []

            for row_number, (_, row) in enumerate(df_upload.iterrows(), start=2):
                csv_code = str(row["商品コード"]).strip()
                item_name = str(row["商品名"]).strip()
                item_required_quantity = int(row["出荷数"])

                try:
                    category_code = normalize_code_part(
                        row["大カテゴリーコード"],
                        "大カテゴリーコード",
                        max_length=8,
                    )
                    category = get_item_category_by_code(
                        conn,
                        project_company["company_id"],
                        category_code,
                        include_inactive=False,
                    )
                    if not category:
                        raise ItemCodeError(
                            f"未登録または無効なカテゴリーコードです：{category_code}"
                        )

                    if csv_code:
                        item_code = csv_code
                        company = project_company
                        year_month = normalize_year_month()
                        parsed = parse_item_code(item_code) or {}
                        sequence = parsed.get("sequence", 0)
                    else:
                        (
                            item_code,
                            company,
                            category,
                            year_month,
                            sequence,
                        ) = reserve_next_item_code(
                            conn,
                            project_id,
                            int(category["id"]),
                            registration_year_month,
                        )

                    if _item_exists(item_code):
                        skipped_items.append(f"{item_code} / {item_name}")
                        continue

                    qr_payload = build_item_qr_payload(
                        item_code=item_code,
                        company_code=company["company_code"],
                        category_code=category["category_code"],
                        year_month=year_month,
                        sequence=sequence,
                        project_code=company.get("project_code", ""),
                        project_id=project_id,
                        item_name=item_name,
                        required_quantity=item_required_quantity,
                    )

                    new_item_id = _insert_item(
                        item_code=item_code,
                        item_name=item_name,
                        project_id=project_id,
                        required_quantity=item_required_quantity,
                        category_code=category["category_code"],
                        category_name=category["category_name"],
                        year_month=year_month,
                        qr_payload=qr_payload,
                    )
                    created_items.append(
                        (new_item_id, item_code, item_name, category["category_code"])
                    )
                except Exception as exc:
                    error_items.append(f"{row_number}行目：{item_name} / {exc}")

            conn.commit()

            for new_item_id, item_code, item_name, category_code in created_items:
                log_action(
                    st.session_state.username,
                    "商品CSV登録",
                    "items",
                    new_item_id,
                    item_name,
                    (
                        f"商品コード: {item_code} / 案件ID: {project_id} / "
                        f"大カテゴリー: {category_code}"
                    ),
                )

            log_action(
                st.session_state.username,
                "商品CSV一括登録",
                "items",
                None,
                selected_project,
                (
                    f"登録: {len(created_items)}件 / "
                    f"重複スキップ: {len(skipped_items)}件 / "
                    f"エラー: {len(error_items)}件"
                ),
            )

            st.success(f"CSV一括登録完了：{len(created_items)}件")
            if skipped_items:
                st.warning(f"重複のためスキップ：{len(skipped_items)}件")
                st.write(skipped_items)
            if error_items:
                st.error(f"登録できなかった行：{len(error_items)}件")
                st.write(error_items)


# ============================================================
# 商品一覧・ラベルCSV
# ============================================================
st.divider()
sort_option = st.selectbox(
    "並び順",
    [
        "ID順",
        "企業コード順",
        "大カテゴリー順",
        "商品コード順",
        "商品名順",
    ],
)

order_by = "items.id"
if sort_option == "企業コード順":
    order_by = "company_code, items.id"
elif sort_option == "大カテゴリー順":
    order_by = "items.major_category_code, items.code"
elif sort_option == "商品コード順":
    order_by = "items.code"
elif sort_option == "商品名順":
    order_by = "items.name"

# FIX: project_options / selected_project compatibility
# 商品管理の簡素化後も、ラベルCSV出力とCSV登録ログで使う案件ラベルを用意する
project_options = {
    (
        f"{_row_value(row, 'company_code', '荷主未設定')}："
        f"{_row_value(row, 'name', '')}"
    ): int(row["id"])
    for row in projects
}

selected_project = (
    f"{project_company.get('company_code', '荷主未設定')}："
    f"{project_company.get('project_name', '')}"
)


st.subheader("シール印刷用CSV出力")
label_project = st.selectbox(
    "CSV出力する案件",
    ["すべて"] + list(project_options.keys()),
)

label_query = """
SELECT
    items.id AS item_id,
    items.code AS item_code,
    items.name AS item_name,
    items.major_category_code AS category_code,
    items.major_category_name AS category_name,
    items.registered_year_month AS registered_year_month,
    items.qr_payload AS qr_payload,
    projects.id AS project_id,
    projects.code AS project_code,
    projects.name AS project_name,
    companies.code AS company_code,
    companies.name AS company_name,
    COALESCE(items.required_quantity, 1) AS required_quantity
FROM items
LEFT JOIN projects
    ON items.project_id = projects.id
LEFT JOIN project_companies
    ON projects.id = project_companies.project_id
LEFT JOIN companies
    ON project_companies.company_id = companies.id
WHERE
    COALESCE(items.is_active, TRUE) = TRUE
    AND COALESCE(projects.is_hidden, FALSE) = FALSE
"""
label_params = []

if label_project != "すべて":
    label_query += """
    AND (COALESCE(companies.code, '荷主未設定') || '：' || projects.name) = ?
    """
    label_params.append(label_project)

label_query += " ORDER BY companies.code, projects.name, items.code"
label_rows = conn.execute(label_query, label_params).fetchall()
label_list = []

for row in label_rows:
    qty = int(_row_value(row, "required_quantity", 1))
    item_code = str(_row_value(row, "item_code", ""))
    parsed = parse_item_code(item_code) or {}
    category_code = str(
        _row_value(row, "category_code", parsed.get("category_code", ""))
    )
    year_month = str(
        _row_value(row, "registered_year_month", parsed.get("year_month", ""))
    )
    sequence = int(parsed.get("sequence", 0))
    qr_payload = str(_row_value(row, "qr_payload", "")).strip()

    if not qr_payload:
        qr_payload = build_item_qr_payload(
            item_code=item_code,
            company_code=str(_row_value(row, "company_code", "")),
            category_code=category_code,
            year_month=year_month or normalize_year_month(),
            sequence=sequence,
            project_code=str(_row_value(row, "project_code", "")),
            project_id=_row_value(row, "project_id", ""),
            item_name=str(_row_value(row, "item_name", "")),
            required_quantity=qty,
        )

    for _ in range(qty):
        label_list.append(
            {
                "企業コード": _row_value(row, "company_code", ""),
                "企業名": _row_value(row, "company_name", ""),
                "大カテゴリーコード": category_code,
                "大カテゴリー": _row_value(row, "category_name", ""),
                "商品コード": item_code,
                "商品名": _row_value(row, "item_name", ""),
                "案件名": _row_value(row, "project_name", ""),
                "出荷数": qty,
                "バーコード文字": qr_payload,
                "QR文字": qr_payload,
            }
        )

df_labels = pd.DataFrame(label_list)

if label_project == "すべて":
    st.info(f"CSV出力対象：全案件 / {len(df_labels)}件")
else:
    st.info(f"CSV出力対象：{label_project} / {len(df_labels)}件")

label_csv = df_labels.to_csv(index=False).encode("cp932", errors="ignore")
st.download_button(
    label="QRシール印刷用CSVを出力",
    data=label_csv,
    file_name="label_print_qr.csv",
    mime="text/csv",
    disabled=df_labels.empty,
)

if df_labels.empty:
    st.info("出力できる商品がありません。")
else:
    st.caption(
        "既存テンプレートが「バーコード文字」列を参照している場合も、"
        "情報入りQR文字列がそのまま入ります。"
    )

st.subheader("商品一覧")
search_text = st.text_input("商品検索")

query = """
SELECT
    items.id AS id,
    companies.code AS company_code,
    companies.name AS company_name,
    projects.name AS project_name,
    items.major_category_code AS category_code,
    items.major_category_name AS category_name,
    items.code AS code,
    items.name AS name,
    items.registered_year_month AS registered_year_month,
    COALESCE(items.required_quantity, 1) AS required_quantity
FROM items
LEFT JOIN projects
    ON items.project_id = projects.id
LEFT JOIN project_companies
    ON projects.id = project_companies.project_id
LEFT JOIN companies
    ON project_companies.company_id = companies.id
WHERE
    COALESCE(items.is_active, TRUE) = TRUE
    AND COALESCE(projects.is_hidden, FALSE) = FALSE
"""
params = []

if search_text:
    query += """
    AND (
        items.code LIKE ?
        OR items.name LIKE ?
        OR COALESCE(items.major_category_code, '') LIKE ?
        OR COALESCE(items.major_category_name, '') LIKE ?
        OR projects.name LIKE ?
        OR companies.code LIKE ?
        OR companies.name LIKE ?
    )
    """
    search_pattern = f"%{search_text}%"
    params.extend([search_pattern] * 7)

query += f" ORDER BY {order_by}"
rows = conn.execute(query, params).fetchall()

item_list = [
    {
        "ID": row["id"],
        "企業コード": _row_value(row, "company_code", ""),
        "企業名": _row_value(row, "company_name", ""),
        "案件名": _row_value(row, "project_name", ""),
        "大カテゴリーコード": _row_value(row, "category_code", ""),
        "大カテゴリー": _row_value(row, "category_name", ""),
        "登録年月": _row_value(row, "registered_year_month", ""),
        "商品コード": row["code"],
        "商品名": row["name"],
        "出荷数": int(_row_value(row, "required_quantity", 1)),
    }
    for row in rows
]

st.dataframe(
    item_list,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# 商品登録の取り消し（論理削除）
# ============================================================
st.divider()
st.subheader("商品登録の取り消し")
st.caption(
    "誤登録した商品を取り消します。データ自体は削除せず無効化するため、"
    "商品コードの採番履歴と操作ログは残ります。"
)

if not item_list:
    st.info("取り消しできる有効な商品がありません。")
else:
    cancel_options = {}
    for item in item_list:
        cancel_label = (
            f"{item['企業コード']} / {item['案件名']} / "
            f"{item['商品コード']} / {item['商品名']}"
        )
        cancel_options[cancel_label] = int(item["ID"])

    selected_cancel_label = st.selectbox(
        "取り消す商品",
        list(cancel_options.keys()),
        key="cancel_registered_item_select",
    )
    selected_cancel_id = cancel_options[selected_cancel_label]
    selected_cancel_item = next(
        item for item in item_list if int(item["ID"]) == selected_cancel_id
    )

    st.warning(
        "取り消すと、この商品は通常の商品一覧・ラベル出力・入出庫の選択対象から外れます。"
        " 過去データは削除しません。"
    )

    cancel_confirmed = st.checkbox(
        (
            f"{selected_cancel_item['商品コード']} / "
            f"{selected_cancel_item['商品名']} を取り消す"
        ),
        key=f"cancel_registered_item_confirm_{selected_cancel_id}",
    )

    if st.button(
        "🗑️ 商品登録を取り消す",
        use_container_width=True,
        disabled=not cancel_confirmed,
        key=f"cancel_registered_item_button_{selected_cancel_id}",
    ):
        try:
            current_item = conn.execute(
                '''
                SELECT id, code, name, project_id
                FROM items
                WHERE id = ?
                  AND COALESCE(is_active, TRUE) = TRUE
                LIMIT 1
                ''',
                (selected_cancel_id,),
            ).fetchone()

            if current_item is None:
                st.info("この商品はすでに取り消されています。")
            else:
                conn.execute(
                    '''
                    UPDATE items
                    SET is_active = FALSE
                    WHERE id = ?
                    ''',
                    (selected_cancel_id,),
                )
                conn.commit()

                log_action(
                    st.session_state.username,
                    "商品登録取消",
                    "items",
                    selected_cancel_id,
                    selected_cancel_item["商品名"],
                    (
                        f"商品コード: {selected_cancel_item['商品コード']} / "
                        f"案件: {selected_cancel_item['案件名']} / 論理削除"
                    ),
                )

                st.success(
                    f"商品登録を取り消しました："
                    f"{selected_cancel_item['商品コード']} / "
                    f"{selected_cancel_item['商品名']}"
                )
                st.rerun()
        except Exception as exc:
            try:
                if hasattr(conn, "rollback"):
                    conn.rollback()
                elif hasattr(conn, "conn"):
                    conn.conn.rollback()
            except Exception:
                pass
            st.error(f"商品取り消しに失敗しました：{exc}")


conn.close()
