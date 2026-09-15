from collections import Counter
from time import perf_counter

import streamlit as st

from auth import check_login
from barcode_serials import (
    normalize_scanned_barcode,
    resolve_scanned_item_code,
)
from database import get_connection


check_login()

st.title("⚡ 高速バーコード読取テスト")
st.warning(
    "テスト専用ページです。ここで読み取っても在庫DBは更新しません。"
)
st.caption(
    "目的：Streamlitのページ全体再実行を避け、読取部分だけを再実行して、"
    "バーコードの連続読取速度を確認します。"
)


def row_to_dict(row):
    return dict(row) if row is not None else None


def safe_text(value):
    return "" if value is None else str(value)


def init_state():
    defaults = {
        "fast_scan_project_id": None,
        "fast_scan_input": "",
        "fast_scan_codes": [],
        "fast_scan_notice": None,
        "fast_scan_last_at": None,
        "fast_scan_intervals": [],
        "fast_scan_item_map": {},
        "fast_scan_required_map": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def clear_scan_state():
    st.session_state.fast_scan_input = ""
    st.session_state.fast_scan_codes = []
    st.session_state.fast_scan_notice = None
    st.session_state.fast_scan_last_at = None
    st.session_state.fast_scan_intervals = []


def add_scan():
    barcode_text = normalize_scanned_barcode(
        st.session_state.fast_scan_input
    )
    st.session_state.fast_scan_input = ""

    if not barcode_text:
        return

    item_map = st.session_state.get("fast_scan_item_map", {})
    required_map = st.session_state.get("fast_scan_required_map", {})
    base_code, unit_number = resolve_scanned_item_code(
        barcode_text,
        item_map,
    )

    if base_code not in item_map:
        st.session_state.fast_scan_notice = (
            "error",
            f"案件違い：{base_code} は選択中の案件の商品ではありません。",
        )
        return

    # A4個別QRは同じ個体番号を二重読取しない。
    if unit_number is not None:
        scanned_units = {
            resolve_scanned_item_code(code, item_map)
            for code in st.session_state.fast_scan_codes
        }
        if (base_code, unit_number) in scanned_units:
            st.session_state.fast_scan_notice = (
                "warning",
                f"このQRは読取済みです：{unit_number}",
            )
            return

    current_counter = Counter(
        resolve_scanned_item_code(code, item_map)[0]
        for code in st.session_state.fast_scan_codes
    )
    if current_counter.get(base_code, 0) >= required_map.get(base_code, 0):
        st.session_state.fast_scan_notice = (
            "warning",
            f"読取超過：{base_code} は案件残数に達しています。",
        )
        return

    now = perf_counter()
    last_at = st.session_state.fast_scan_last_at
    if last_at is not None:
        interval = now - last_at
        intervals = st.session_state.fast_scan_intervals
        intervals.append(interval)
        # 表示用なので直近20件だけ保持。
        st.session_state.fast_scan_intervals = intervals[-20:]
    st.session_state.fast_scan_last_at = now

    st.session_state.fast_scan_codes.append(barcode_text)
    st.session_state.fast_scan_notice = (
        "success",
        f"読取：{barcode_text}",
    )


init_state()

conn = get_connection()
projects = conn.execute(
    """
    SELECT id, code, name
    FROM projects
    WHERE COALESCE(is_hidden, FALSE) = FALSE
    ORDER BY name
    """
).fetchall()
projects = [row_to_dict(row) for row in projects]

if not projects:
    conn.close()
    st.warning("案件が登録されていません。")
    st.stop()

project_labels = {
    f"{project['code']} - {project['name']}": project["id"]
    for project in projects
}

selected_label = st.selectbox(
    "テストする案件",
    list(project_labels.keys()),
    key="fast_scan_project_select",
)
project_id = project_labels[selected_label]

if st.session_state.fast_scan_project_id != project_id:
    st.session_state.fast_scan_project_id = project_id
    clear_scan_state()

# 商品ごとにstock_logsを問い合わせるのではなく、1本の集計SQLで取得する。
out_items = conn.execute(
    """
    SELECT
        i.id,
        i.code,
        i.name,
        COALESCE(i.required_quantity, 1) AS required_quantity,
        COALESCE(
            SUM(
                CASE
                    WHEN sl.type = '出庫' AND sl.qty < 0
                    THEN -sl.qty
                    ELSE 0
                END
            ),
            0
        ) AS already_shipped
    FROM items i
    LEFT JOIN stock_logs sl
      ON sl.project_id = i.project_id
     AND sl.item_id = i.id
    WHERE i.project_id = ?
      AND COALESCE(i.is_active, TRUE) = TRUE
    GROUP BY i.id, i.code, i.name, i.required_quantity
    ORDER BY i.code
    """,
    (project_id,),
).fetchall()
out_items = [row_to_dict(row) for row in out_items]
conn.close()

if not out_items:
    st.warning("この案件には有効な商品がありません。")
    st.stop()

for item in out_items:
    planned_qty = max(1, int(item["required_quantity"] or 1))
    already_shipped = int(item["already_shipped"] or 0)
    item["required_quantity"] = planned_qty
    item["already_shipped"] = already_shipped
    item["remaining_quantity"] = max(planned_qty - already_shipped, 0)

item_map = {
    safe_text(item["code"]): item
    for item in out_items
}
required_map = {
    safe_text(item["code"]): item["remaining_quantity"]
    for item in out_items
}
st.session_state.fast_scan_item_map = item_map
st.session_state.fast_scan_required_map = required_map

st.info(
    f"案件：{selected_label}　商品種類：{len(out_items):,}品"
)
st.caption(
    "下の入力欄を1回クリックしてから、できるだけ間を空けず連続で読んでください。"
)


@st.fragment
def fast_scan_panel():
    st.text_input(
        "バーコード読み取り",
        key="fast_scan_input",
        placeholder="ここを1回クリックしてから連続スキャン",
        on_change=add_scan,
    )

    notice = st.session_state.fast_scan_notice
    if notice:
        notice_type, message = notice
        if notice_type == "error":
            st.error(message)
        elif notice_type == "warning":
            st.warning(message)
        else:
            st.success(message)

    scanned_codes = st.session_state.fast_scan_codes
    scan_counter = Counter(
        resolve_scanned_item_code(code, item_map)[0]
        for code in scanned_codes
    )

    intervals = st.session_state.fast_scan_intervals
    avg_interval = (
        sum(intervals) / len(intervals)
        if intervals
        else None
    )

    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("読取数", f"{len(scanned_codes):,}個")
    metric2.metric(
        "商品種類",
        f"{sum(1 for qty in scan_counter.values() if qty > 0):,}品",
    )
    metric3.metric(
        "平均スキャン間隔",
        "-" if avg_interval is None else f"{avg_interval:.2f}秒",
    )

    rows = []
    for code, qty in scan_counter.items():
        item = item_map.get(code)
        if item is None:
            continue
        rows.append(
            {
                "商品コード": code,
                "商品名": item["name"],
                "読取": qty,
                "案件残数": item["remaining_quantity"],
            }
        )

    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.info("まだ読み取っていません。")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "↩️ 直前の読取を取り消す",
            disabled=not scanned_codes,
            use_container_width=True,
        ):
            removed = st.session_state.fast_scan_codes.pop()
            st.session_state.fast_scan_notice = (
                "success",
                f"取り消しました：{removed}",
            )
            st.rerun(scope="fragment")

    with col2:
        if st.button(
            "🗑 テスト結果をリセット",
            disabled=not scanned_codes,
            use_container_width=True,
        ):
            clear_scan_state()
            st.rerun(scope="fragment")

    if scanned_codes:
        st.caption(
            "※ このページではDB更新・出庫確定は行いません。"
            "読取速度と取りこぼし確認だけを行います。"
        )


fast_scan_panel()
