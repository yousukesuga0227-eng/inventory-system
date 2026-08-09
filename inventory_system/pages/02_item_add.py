from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import streamlit as st

from auth import check_login
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

check_login()
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

st.title("➕ 案件・商品追加")
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

active_categories = list_item_categories(
    conn,
    project_company["company_id"],
    include_inactive=False,
)

if not active_categories:
    st.warning(
        "登録済みの大カテゴリーがありません。管理者へ登録を依頼してください。"
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


conn.close()
