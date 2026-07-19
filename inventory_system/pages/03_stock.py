import streamlit as st
from database import get_connection
from auth import check_login
from barcode_serials import normalize_scanned_barcode, split_unit_barcode

check_login()
conn = get_connection()

st.title("入出庫登録")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

# 案件取得
projects = conn.execute(
    """
    SELECT *
    FROM projects
    WHERE
        COALESCE(is_hidden, FALSE) = FALSE
    ORDER BY name
    """
).fetchall()

if not projects:
    st.warning("先に案件を登録してください")
    st.stop()

# 案件辞書
project_map = {
    f"{p['code']} - {p['name']}": p["id"]
    for p in projects
}

# 種別
stock_type = st.radio(
    "種別",
    ["入庫", "出庫"],
    horizontal=True
)

# 案件選択
selected_project = st.selectbox(
    "案件",
    list(project_map.keys())
)

project_id = project_map[selected_project]

# 選択した案件の商品だけ取得
items = conn.execute(
    """
    SELECT *
    FROM items
    WHERE
        project_id = ?
        AND COALESCE(is_active, TRUE) = TRUE
    ORDER BY code
    """,
    (project_id,)
).fetchall()

if not items:
    st.warning("この案件には商品が登録されていません")
    st.stop()

# 商品候補
item_options = [
    f"{i['code']} - {i['name']}"
    for i in items
]

item_map = {
    f"{i['code']} - {i['name']}": i["id"]
    for i in items
}

code_map = {
    i["code"]: f"{i['code']} - {i['name']}"
    for i in items
}

st.subheader("商品指定")

# バーコード入力
barcode = st.text_input(
    "バーコード / 商品コード",
    placeholder="バーコードを読み取るか、商品コードを入力"
)

barcode_item = None

if barcode:
    clean_barcode = normalize_scanned_barcode(barcode)
    base_code, unit_number = split_unit_barcode(clean_barcode)

    if base_code in code_map:
        barcode_item = code_map[base_code]
        st.success(f"バーコード一致：{barcode_item}")

        if unit_number is not None:
            st.caption(f"個体No.：{unit_number:03d}")
    else:
        st.warning("バーコードに一致する商品がありません")

# 商品選択
default_index = 0

if barcode_item in item_options:
    default_index = item_options.index(barcode_item)

selected_item = st.selectbox(
    "商品選択",
    item_options,
    index=default_index
)

item_id = item_map[selected_item]

# 現在庫表示
current_stock = conn.execute(
    """
    SELECT
        COALESCE(SUM(qty), 0)
    FROM stock_logs
    WHERE
        project_id = ?
        AND item_id = ?
    """,
    (
        project_id,
        item_id
    )
).fetchone()[0]

st.info(
    f"現在庫：{current_stock}"
)

# 数量
qty = st.number_input(
    "数量",
    min_value=1,
    step=1
)

# 登録
if st.button("登録"):

    save_qty = qty

    # 出庫時在庫チェック
    if stock_type == "出庫":

        if qty > current_stock:

            st.error(
                f"在庫不足：現在庫は {current_stock} です"
            )

            st.stop()

        save_qty = -qty

    # DB登録
    conn.execute(
        """
        INSERT INTO stock_logs(
        project_id,
        item_id,
        qty,
        type,
        username
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
        project_id,
        item_id,
        save_qty,
        stock_type,
        st.session_state.username
        )   
    )

    conn.commit()

    st.success(
        f"{stock_type}登録完了：{selected_item}"
    )
