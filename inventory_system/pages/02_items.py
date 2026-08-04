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
# 案件・荷主選択
# ============================================================
projects = conn.execute(
    """
    SELECT
        p.id,
        p.code,
        p.name,
        c.code AS company_code,
        c.name AS company_name
    FROM projects p
    LEFT JOIN project_companies pc
        ON p.id = pc.project_id
    LEFT JOIN companies c
        ON pc.company_id = c.id
    WHERE COALESCE(p.is_hidden, FALSE) = FALSE
    ORDER BY c.code, p.name
    """
).fetchall()

project_options = {
    (
        f"{_row_value(project, 'company_code', '荷主未設定')}："
        f"{_row_value(project, 'name', '')}"
    ): int(project["id"])
    for project in projects
}

if not project_options:
    st.warning("先に案件を登録してください。")
    st.stop()

selected_project = st.selectbox(
    "案件選択",
    list(project_options.keys()),
)
project_id = project_options[selected_project]

try:
    project_company = get_project_company(conn, project_id)
except ItemCodeError as exc:
    st.error(str(exc))
    st.info("企業管理と案件管理で、案件に荷主コードを設定してください。")
    st.stop()

st.caption(
    f"荷主：{project_company['company_code']} / "
    f"{project_company.get('company_name', '')}"
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

conn.close()
