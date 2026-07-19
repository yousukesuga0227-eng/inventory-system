import streamlit as st
from database import get_connection
from auth import check_login
from barcode_serials import normalize_scanned_barcode, split_unit_barcode

check_login()

conn = get_connection()

st.title("商品検索")
st.success(
    f"ログイン中：{st.session_state.get('display_name', st.session_state.username)}"
)

# バーコード入力
barcode = st.text_input(
    "バーコード入力"
)

if barcode:
    clean_barcode = normalize_scanned_barcode(barcode)
    base_code, unit_number = split_unit_barcode(clean_barcode)

    query = """
    SELECT
        i.code AS item_code,
        i.name AS item_name,
        p.code AS project_code,
        p.name AS project_name

    FROM items i

    LEFT JOIN projects p
    ON i.project_id = p.id

    WHERE
        i.code = ?
    """

    row = conn.execute(
        query,
        (base_code,)
    ).fetchone()

    if row:

        st.success("商品発見")

        st.write(f"商品コード: {row['item_code']}")

        if unit_number is not None:
            st.write(f"個体No.: {unit_number:03d}")

        st.write(f"商品名: {row['item_name']}")
        st.write(f"案件コード: {row['project_code']}")
        st.write(f"案件名: {row['project_name']}")

    else:

        st.error("商品が見つかりません")
