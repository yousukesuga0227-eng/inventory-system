import streamlit as st
import os
import barcode
from barcode.writer import ImageWriter
from database import get_connection, log_action
from auth import check_admin
import pandas as pd
from io import BytesIO
from datetime import datetime

check_admin()

conn = get_connection()

st.title("商品管理")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)


def generate_item_code(conn, project_id):
    project = conn.execute(
        """
        SELECT
            p.id,
            p.code AS project_code,
            c.code AS company_code
        FROM projects p
        LEFT JOIN project_companies pc
            ON p.id = pc.project_id
        LEFT JOIN companies c
            ON pc.company_id = c.id
        WHERE p.id = ?
        """,
        (project_id,)
    ).fetchone()

    ym = datetime.now().strftime("%y%m")

    if project and project["company_code"]:
        company_code = str(project["company_code"]).upper().strip()
    else:
        company_code = "0000"

    company_code = (
        company_code
        .replace("-", "")
        [:4]
        .zfill(4)
    )

    prefix = f"{company_code}-{ym}"

    row = conn.execute(
        """
        SELECT code
        FROM items
        WHERE code LIKE ?
        ORDER BY code DESC
        LIMIT 1
        """,
        (f"{prefix}-%",)
    ).fetchone()

    if row:
        try:
            next_number = int(str(row["code"]).split("-")[-1]) + 1
        except Exception:
            next_number = 1
    else:
        next_number = 1

    return f"{prefix}-{next_number:04d}"


def create_barcode(project_code, item_code):
    barcode_data = f"{project_code}_{item_code}"

    os.makedirs("barcodes/project_items", exist_ok=True)

    barcode_class = barcode.get_barcode_class("code128")
    barcode_obj = barcode_class(
        barcode_data,
        writer=ImageWriter()
    )

    barcode_obj.save(f"barcodes/project_items/{barcode_data}")

    return barcode_data


projects = conn.execute(
    """
    SELECT
        p.*,
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
    f"{project['company_code'] or '企業未設定'}：{project['name']}": project["id"]
    for project in projects
}

if not project_options:
    st.warning("先に案件を登録してください")
    st.stop()

selected_project = st.selectbox(
    "案件選択",
    list(project_options.keys())
)

project_id = project_options[selected_project]

preview_code = generate_item_code(conn, project_id)

st.info(f"次に発行される商品コード：{preview_code}")

name = st.text_input("商品名", key="new_item_name")

required_quantity = st.number_input(
    "出荷数",
    min_value=1,
    value=1,
    step=1
)

if st.button("商品登録"):

    if not name.strip():
        st.warning("商品名を入力してください")

    else:
        code = generate_item_code(conn, project_id)

        exists = conn.execute(
            """
            SELECT id
            FROM items
            WHERE code = ?
            """,
            (code,)
        ).fetchone()

        if exists:
            st.error("商品コードが重複しました。もう一度登録ボタンを押してください。")
            st.stop()

        cursor = conn.execute(
            """
            INSERT INTO items(
                code,
                name,
                project_id,
                required_quantity
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                code,
                name.strip(),
                project_id,
                required_quantity
            )
        )

        new_item_id = cursor.lastrowid
        conn.commit()

        log_action(
            st.session_state.username,
            "商品登録",
            "items",
            new_item_id,
            name.strip(),
            f"商品コード: {code} / 案件ID: {project_id}"
        )

        project_row = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        create_barcode(project_row["code"], code)

        st.success(f"商品登録完了：{code} / {name.strip()}")


st.subheader("CSVで商品一括登録")

st.info(
    "CSVの列名は「商品名」「出荷数」にしてください。"
    "「商品コード」列がある場合はそのコードを使用、空欄なら自動採番します。"
)

sample_df = pd.DataFrame(
    [
        {"商品コード": "", "商品名": "Falcon ダイニングチェア", "出荷数": 4},
        {"商品コード": "", "商品名": "Tsubomi テーブル", "出荷数": 1},
    ]
)

sample_csv = sample_df.to_csv(index=False).encode("cp932", errors="ignore")

st.download_button(
    label="サンプルCSVをダウンロード",
    data=sample_csv,
    file_name="items_sample.csv",
    mime="text/csv"
)

uploaded_file = st.file_uploader(
    "商品CSVをアップロード",
    type=["csv"]
)

if uploaded_file is not None:

    csv_bytes = uploaded_file.getvalue()

    try:
        df_upload = pd.read_csv(BytesIO(csv_bytes), encoding="cp932")
    except UnicodeDecodeError:
        df_upload = pd.read_csv(BytesIO(csv_bytes), encoding="utf-8-sig")

    df_upload.columns = [str(col).strip() for col in df_upload.columns]

    if "商品コード" not in df_upload.columns:
        df_upload["商品コード"] = ""

    if "出荷数" not in df_upload.columns:
        df_upload["出荷数"] = 1

    if "商品名" not in df_upload.columns:
        st.error("CSVに必要な列がありません: 商品名")

    else:
        df_upload = df_upload[
            ["商品コード", "商品名", "出荷数"]
        ].fillna("")

        df_upload["商品コード"] = df_upload["商品コード"].astype(str).str.strip()
        df_upload["商品名"] = df_upload["商品名"].astype(str).str.strip()

        df_upload["出荷数"] = pd.to_numeric(
            df_upload["出荷数"],
            errors="coerce"
        ).fillna(1).astype(int)

        df_upload.loc[df_upload["出荷数"] < 1, "出荷数"] = 1
        df_upload = df_upload[df_upload["商品名"] != ""]

        st.write(f"読み込み件数：{len(df_upload)}件")

        st.dataframe(
            df_upload,
            use_container_width=True,
            hide_index=True
        )

        if st.button("CSVの商品を一括登録"):

            created_count = 0
            skipped_items = []

            project_row = conn.execute(
                """
                SELECT *
                FROM projects
                WHERE id = ?
                """,
                (project_id,)
            ).fetchone()

            project_code = project_row["code"]

            for _, row in df_upload.iterrows():

                csv_code = str(row["商品コード"]).strip()
                item_name = str(row["商品名"]).strip()
                item_required_quantity = int(row["出荷数"])

                if csv_code:
                    item_code = csv_code
                else:
                    item_code = generate_item_code(conn, project_id)

                exists = conn.execute(
                    """
                    SELECT id
                    FROM items
                    WHERE code = ?
                    """,
                    (item_code,)
                ).fetchone()

                if exists:
                    skipped_items.append(f"{item_code} / {item_name}")
                    continue

                cursor = conn.execute(
                    """
                    INSERT INTO items(
                        code,
                        name,
                        project_id,
                        required_quantity
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        item_code,
                        item_name,
                        project_id,
                        item_required_quantity
                    )
                )

                new_item_id = cursor.lastrowid

                log_action(
                    st.session_state.username,
                    "商品CSV登録",
                    "items",
                    new_item_id,
                    item_name,
                    f"商品コード: {item_code} / 案件ID: {project_id}"
                )

                create_barcode(project_code, item_code)

                created_count += 1

            conn.commit()

            log_action(
                st.session_state.username,
                "商品CSV一括登録",
                "items",
                None,
                selected_project,
                f"登録: {created_count}件 / スキップ: {len(skipped_items)}件"
            )

            st.success(f"CSV一括登録完了：{created_count}件")

            if skipped_items:
                st.warning(f"重複のためスキップ：{len(skipped_items)}件")
                st.write(skipped_items)


sort_option = st.selectbox(
    "並び順",
    ["ID順", "企業コード順", "商品コード順", "商品名順"]
)

order_by = "items.id"

if sort_option == "企業コード順":
    order_by = "company_code"
elif sort_option == "商品コード順":
    order_by = "items.code"
elif sort_option == "商品名順":
    order_by = "items.name"

st.subheader("商品一覧")

st.subheader("シール印刷用CSV出力")

label_project = st.selectbox(
    "CSV出力する案件",
    ["すべて"] + list(project_options.keys())
)

label_query = """
SELECT
    items.code AS 商品コード,
    items.name AS 商品名,
    projects.name AS 案件名,
    companies.code AS 企業コード,
    companies.name AS 企業名,
    COALESCE(items.required_quantity,1) AS 出荷数
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
    AND (COALESCE(companies.code, '企業未設定') || '：' || projects.name) = ?
    """
    label_params.append(label_project)

label_query += """
ORDER BY companies.code, projects.name, items.code
"""

label_rows = conn.execute(label_query, label_params).fetchall()

label_list = []

for row in label_rows:

    qty = int(row["出荷数"])

    for _ in range(qty):
        label_list.append(
            {
                "企業コード": row["企業コード"],
                "企業名": row["企業名"],
                "商品コード": row["商品コード"],
                "商品名": row["商品名"],
                "案件名": row["案件名"],
                "出荷数": row["出荷数"],
                "バーコード文字": str(row["商品コード"])
            }
        )

df_labels = pd.DataFrame(label_list)

if label_project == "すべて":
    st.info(f"CSV出力対象：全案件 / {len(df_labels)}件")
else:
    st.info(f"CSV出力対象：{label_project} / {len(df_labels)}件")

csv = df_labels.to_csv(index=False).encode("cp932", errors="ignore")

st.download_button(
    label="シール印刷用CSVを出力",
    data=csv,
    file_name="label_print.csv",
    mime="text/csv",
    disabled=df_labels.empty
)

if df_labels.empty:
    st.info("出力できる商品がありません")


search_text = st.text_input("商品検索")

query = """
SELECT
    items.id AS id,
    companies.code AS company_code,
    companies.name AS company_name,
    projects.name AS project_name,
    items.code AS code,
    items.name AS name,
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
        OR projects.name LIKE ?
        OR companies.code LIKE ?
        OR companies.name LIKE ?
    )
    """

    params.extend([
        f"%{search_text}%",
        f"%{search_text}%",
        f"%{search_text}%",
        f"%{search_text}%",
        f"%{search_text}%"
    ])

query += f"""
ORDER BY {order_by}
"""

rows = conn.execute(query, params).fetchall()

item_list = []

for row in rows:
    item_list.append(
        {
            "ID": row["id"],
            "企業コード": row["company_code"],
            "企業名": row["company_name"],
            "案件名": row["project_name"],
            "商品コード": row["code"],
            "商品名": row["name"],
            "出荷数": int(row["required_quantity"] or 1)
        }
    )

st.dataframe(
    item_list,
    use_container_width=True
)

conn.close()