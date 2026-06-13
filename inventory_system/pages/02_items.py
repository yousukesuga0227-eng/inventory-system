import streamlit as st
import os
import barcode
from barcode.writer import ImageWriter
from database import get_connection, log_action
from auth import check_admin
import pandas as pd
from io import BytesIO

check_admin()


conn = get_connection()

st.title("商品管理")
st.success(
    f"ログイン中：{st.session_state.username}"
)

# 案件一覧取得
projects = conn.execute(
    """
    SELECT *
    FROM projects
    WHERE
        COALESCE(is_hidden, FALSE) = FALSE
    ORDER BY name
    """
).fetchall()

# 商品コード
code = st.text_input(
    "商品コード",
    key="new_item_code"
)
# 商品名
name = st.text_input(
    "商品名",
    key="new_item_name"
)

required_quantity = st.number_input(
    "商品小口数",
    min_value=1,
    value=1,
    step=1
)

# 案件選択
project_options = {
    project["name"]: project["id"]
    for project in projects
}

if not project_options:

    st.warning(
        "先に案件を登録してください"
    )

    st.stop()
    

selected_project = st.selectbox(
    "案件選択",
    list(project_options.keys())
)

project_id = project_options[
    selected_project
]

if st.button("商品登録"):

    if not code or not name:

        st.warning(
            "商品コードと商品名を入力してください"
        )

    else:

        # 商品登録
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
                name,
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
            name,
            f"商品コード: {code} / 案件ID: {project_id}"
        )

        # 案件コード取得
        project_row = conn.execute(
            """
            SELECT *
            FROM projects
            WHERE id = ?
            """,
            (project_id,)
        ).fetchone()

        project_code = project_row["code"]

        # 案件 + 商品コード
        barcode_data = (
            f"{project_code}_{code}"
        )

        # 保存フォルダ作成
        os.makedirs(
            "barcodes/project_items",
            exist_ok=True
        )

        # バーコード生成
        barcode_class = barcode.get_barcode_class(
            "code128"
        )

        barcode_obj = barcode_class(
            barcode_data,
            writer=ImageWriter()
        )

        # 保存
        barcode_obj.save(
            f"barcodes/project_items/{barcode_data}"
        )

        st.success(
            "商品 + 案件バーコード生成完了"
        )

# =====================
# CSV一括登録
# =====================

st.subheader("CSVで商品一括登録")

st.info(
    "CSVの列名は「商品コード」「商品名」「商品小口数」にしてください。商品小口数が無い場合は1で登録されます。"
)

# サンプルCSVダウンロード
sample_df = pd.DataFrame(
    [
        {
            "商品コード": "05-005",
            "商品名": "Falcon ダイニングチェア",
            "商品小口数": 4
        },
        {
            "商品コード": "05-006",
            "商品名": "Tsubomi テーブル",
            "商品小口数": 1
        }
    ]
)

sample_csv = sample_df.to_csv(
    index=False
).encode("cp932", errors="ignore")

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
        df_upload = pd.read_csv(
            BytesIO(csv_bytes),
            encoding="cp932"
        )

    except UnicodeDecodeError:
        df_upload = pd.read_csv(
            BytesIO(csv_bytes),
            encoding="utf-8-sig"
        )

    # 列名の空白除去
    df_upload.columns = [
        str(col).strip()
        for col in df_upload.columns
    ]

    required_columns = [
        "商品コード",
        "商品名"
    ]
    if "商品小口数" not in df_upload.columns:
        df_upload["商品小口数"] = 1

    missing_columns = [
        col
        for col in required_columns
        if col not in df_upload.columns
    ]

    if missing_columns:

        st.error(
            f"CSVに必要な列がありません: {', '.join(missing_columns)}"
        )

    else:

        df_upload = df_upload[
        required_columns + ["商品小口数"]
        ].fillna("")

        df_upload["商品コード"] = (
            df_upload["商品コード"]
            .astype(str)
            .str.strip()
        )

        df_upload["商品名"] = (
            df_upload["商品名"]
            .astype(str)
            .str.strip()
        )

        df_upload["商品小口数"] = pd.to_numeric(
            df_upload["商品小口数"],
            errors="coerce"
        ).fillna(1).astype(int)

        df_upload.loc[
            df_upload["商品小口数"] < 1,
            "商品小口数"
        ] = 1

        # 空行除外
        df_upload = df_upload[
            (df_upload["商品コード"] != "")
            & (df_upload["商品名"] != "")
        ]

        st.write(
            f"読み込み件数：{len(df_upload)}件"
        )

        st.dataframe(
            df_upload,
            use_container_width=True,
            hide_index=True
        )

        if st.button("CSVの商品を一括登録"):

            created_count = 0
            skipped_items = []

            for _, row in df_upload.iterrows():

                item_code = row["商品コード"]
                item_name = row["商品名"]
                item_required_quantity = int(row["商品小口数"])

                # 同じ案件内で商品コード重複チェック
                exists = conn.execute(
                    """
                    SELECT id
                    FROM items
                    WHERE project_id = ?
                      AND code = ?
                    """,
                    (
                        project_id,
                        item_code
                    )
                ).fetchone()

                if exists:

                    skipped_items.append(
                        f"{item_code} / {item_name}"
                    )

                    continue

                conn.execute(
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

            st.success(
                f"CSV一括登録完了：{created_count}件"
            )

            if skipped_items:

                st.warning(
                    f"重複のためスキップ：{len(skipped_items)}件"
                )

                st.write(skipped_items)

sort_option = st.selectbox(
    "並び順",
    [
        "ID順",
        "商品コード順",
"商品名順"
    ]
)

order_by = "items.id"

if sort_option == "商品コード順":

    order_by = "items.code"

elif sort_option == "商品名順":

    order_by = "items.name"

st.subheader("商品一覧")

st.subheader("シール印刷用CSV出力")

# 案件で絞り込み
label_project = st.selectbox(
    "CSV出力する案件",
    ["すべて"] + list(project_options.keys())
)

label_query = """
SELECT
    items.code AS 商品コード,
    items.name AS 商品名,
    projects.name AS 案件名,
    COALESCE(items.required_quantity,1) AS 商品小口数
FROM items
LEFT JOIN projects
    ON items.project_id = projects.id
WHERE
    COALESCE(items.is_active, TRUE) = TRUE
    AND COALESCE(projects.is_hidden, FALSE) = FALSE
"""

label_params = []

if label_project != "すべて":
    label_query += """
    AND projects.name = ?
    """
    label_params.append(label_project)

label_query += """
ORDER BY projects.name, items.code
"""

label_rows = conn.execute(
    label_query,
    label_params
).fetchall()

label_list = []

for row in label_rows:

    qty = int(row["商品小口数"])

    for _ in range(qty):

        label_list.append(
            {
                "商品コード": row["商品コード"],
                "商品名": row["商品名"],
                "案件名": row["案件名"],
                "商品小口数": row["商品小口数"],
                "バーコード文字": str(row["商品コード"])
            }
        )

df_labels = pd.DataFrame(label_list)

if label_project == "すべて":
    st.info(
        f"CSV出力対象：全案件 / {len(df_labels)}件"
    )
else:
    st.info(
        f"CSV出力対象：{label_project} / {len(df_labels)}件"
    )

csv = df_labels.to_csv(
    index=False
).encode("cp932", errors="ignore")

st.download_button(
    label="シール印刷用CSVを出力",
    data=csv,
    file_name="label_print.csv",
    mime="text/csv",
    disabled=df_labels.empty
)

if df_labels.empty:
    st.info("出力できる商品がありません")

    
# 検索
search_text = st.text_input(
    "商品検索"
)

query = """
SELECT
    items.id AS id,
    projects.name AS project_name,
    items.code AS code,
    items.name AS name,
    COALESCE(items.required_quantity, 1) AS required_quantity
FROM items
LEFT JOIN projects
    ON items.project_id = projects.id
WHERE
    COALESCE(items.is_active, TRUE) = TRUE
    AND COALESCE(projects.is_hidden, FALSE) = FALSE
"""

params = []

# 検索条件
if search_text:

    query += """
    AND (
    items.code LIKE ?
    OR items.name LIKE ?
    OR projects.name LIKE ?
)
    """

    params.append(
        f"%{search_text}%"
    )

    params.append(
        f"%{search_text}%"
    )
    params.append(
    f"%{search_text}%"
    )


# 並び順
query += f"""
ORDER BY {order_by}
"""

rows = conn.execute(
    query,
    params
).fetchall()

item_list = []

for row in rows:

    
        item_list.append(
    {
        "ID": row["id"],
        "案件名": row["project_name"],
        "商品コード": row["code"],
        "商品名": row["name"],
        "商品小口数": int(row["required_quantity"] or 1)
    }
)
     

st.dataframe(
    item_list,
    use_container_width=True
)