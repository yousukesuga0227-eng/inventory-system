from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from auth import check_login
from database import get_connection
from pages.pallet.pallet_db import (
    PalletError,
    create_pallet_batch,
    delete_pallet_batch,
    get_batch_pallets,
    get_items_for_company,
    get_pallet_by_code,
    list_editable_pallet_batches,
    list_pallet_history,
    list_pallet_stock,
    ship_pallet,
    update_pallet_batch,
)
from pages.pallet.pallet_documents import create_pallet_a4_pdf
from pages.pallet.pallet_tables import (
    PalletSchemaError,
    validate_pallet_database,
)


JST = timezone(timedelta(hours=9), name="JST")


# ============================================================
# ログイン・DB
# ============================================================
check_login()

conn = get_connection()

try:
    validate_pallet_database(conn)
except PalletSchemaError as exc:
    st.error(str(exc))
    st.info(
        "SHARKを停止し、同梱の再構築スクリプトを"
        "一度だけ実行してから再起動してください。"
    )
    conn.close()
    st.stop()


# ============================================================
# ページ基本表示
# ============================================================
st.title("在庫品・パレット管理")

display_name = st.session_state.get(
    "display_name",
    st.session_state.get("username", ""),
)
username = st.session_state.get("username", display_name)

st.success(f"ログイン中：{display_name}")


# ============================================================
# セッション初期化
# ============================================================
SESSION_DEFAULTS = {
    "pallet_preview": None,
    "pallet_received_qty": 1,
    "pallet_count": 1,
    "pallet_allocation_mode": "自動",
    "pallet_editor_version": 0,
    "pallet_last_pdf": None,
    "pallet_last_pdf_name": "",
    "pallet_last_batch_code": "",
    "pallet_receiving_flash": "",
    "pallet_shipping_target": None,
    "pallet_shipping_flash": "",
    "pallet_history_flash": "",
    "pallet_history_pdf": None,
    "pallet_history_pdf_name": "",
    "pallet_history_pdf_batch_code": "",
}

for session_key, default_value in SESSION_DEFAULTS.items():
    if session_key not in st.session_state:
        st.session_state[session_key] = default_value

if st.session_state.pop("pallet_reset_inputs", False):
    st.session_state.pallet_received_qty = 1
    st.session_state.pallet_count = 1
    st.session_state.pallet_allocation_mode = "自動"


# ============================================================
# 共通関数
# ============================================================
def row_value(row, key, default=None):
    """sqlite3.Row / dictのどちらでも安全に値を取得する。"""

    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default

    return default if value is None else value


def format_datetime(value):
    """DB日時を日本時間の表示文字列へ変換する。"""

    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(JST)
        return value.strftime("%Y-%m-%d %H:%M")

    text = str(value or "").strip()

    if not text:
        return ""

    normalized = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16].replace("T", " ")


def create_auto_allocation(received_qty, pallet_count):
    """
    入庫個数をパレット枚数へ自動で割り振る。

    95個・10枚
      → 10,10,10,10,10,9,9,9,9,9
    """

    base_qty = received_qty // pallet_count
    remainder = received_qty % pallet_count
    allocations = []

    for index in range(1, pallet_count + 1):
        quantity = base_qty

        if index <= remainder:
            quantity += 1

        allocations.append(
            {
                "パレット番号": (
                    f"{index:03d} / {pallet_count:03d}"
                ),
                "個数": int(quantity),
            }
        )

    return allocations


def clear_preview():
    st.session_state.pallet_preview = None
    st.session_state.pallet_editor_version += 1


def company_changed():
    clear_preview()
    st.session_state.pop("pallet_selected_item", None)


def stock_dataframe(rows):
    data = []

    for row in rows:
        data.append(
            {
                "パレットコード": row_value(
                    row,
                    "pallet_code",
                    "",
                ),
                "パレット番号": (
                    f"{int(row_value(row, 'pallet_sequence', 0)):03d}"
                    f" / "
                    f"{int(row_value(row, 'total_pallets', 0)):03d}"
                ),
                "荷主": row_value(row, "company_name", ""),
                "案件": row_value(row, "project_name", ""),
                "商品コード": row_value(row, "item_code", ""),
                "商品名": row_value(row, "item_name", ""),
                "入庫数": int(row_value(row, "initial_qty", 0)),
                "現在庫": int(row_value(row, "current_qty", 0)),
                "状態": row_value(row, "status", ""),
                "保管場所": row_value(row, "location", ""),
                "入庫日時": format_datetime(
                    row_value(row, "created_at", "")
                ),
                "更新日時": format_datetime(
                    row_value(row, "updated_at", "")
                ),
            }
        )

    return pd.DataFrame(data)


def history_dataframe(rows):
    data = []

    for row in rows:
        data.append(
            {
                "日時": format_datetime(
                    row_value(row, "created_at", "")
                ),
                "区分": row_value(row, "history_type", ""),
                "登録No／パレット": row_value(
                    row,
                    "pallet_code",
                    "",
                ),
                "荷主": row_value(row, "company_name", ""),
                "案件": row_value(row, "project_name", ""),
                "商品コード": row_value(row, "item_code", ""),
                "商品名": row_value(row, "item_name", ""),
                "商品数": int(row_value(row, "qty", 0)),
                "処理前商品数": int(
                    row_value(row, "before_qty", 0)
                ),
                "処理後商品数": int(
                    row_value(row, "after_qty", 0)
                ),
                "担当者": row_value(row, "username", ""),
                "備考": row_value(row, "remarks", ""),
            }
        )

    return pd.DataFrame(data)


# ============================================================
# タブ
# ============================================================
tab_receiving, tab_shipping, tab_stock, tab_history = st.tabs(
    [
        "入庫登録",
        "QR出庫",
        "在庫確認",
        "入出庫履歴",
    ]
)


# ============================================================
# 入庫登録
# ============================================================
with tab_receiving:
    st.subheader("入庫登録")

    st.info(
        "荷主・商品・入庫数量・パレット枚数を入力し、"
        "パレットごとの数量を作成します。"
    )
    st.caption(
        "荷主と案件は既存DB上で直接紐づいていないため、"
        "荷主と商品はそれぞれ選択してください。"
    )

    if st.session_state.pallet_receiving_flash:
        st.success(st.session_state.pallet_receiving_flash)
        st.session_state.pallet_receiving_flash = ""

    if st.session_state.pallet_last_pdf is not None:
        st.download_button(
            "📄 登録したパレットのA4票をダウンロード",
            data=st.session_state.pallet_last_pdf,
            file_name=st.session_state.pallet_last_pdf_name,
            mime="application/pdf",
            use_container_width=True,
        )
        st.caption(
            "A4票は1パレットにつき1ページです。"
            f" 登録No：{st.session_state.pallet_last_batch_code}"
        )

    companies = conn.execute(
        """
        SELECT
            id,
            code,
            name
        FROM companies
        ORDER BY
            code,
            name
        """
    ).fetchall()

    if not companies:
        st.warning("企業マスターに荷主が登録されていません。")

    else:
        company_map = {}

        for company in companies:
            company_id = row_value(company, "id")
            company_code = str(
                row_value(company, "code", "") or ""
            ).strip()
            company_name = str(
                row_value(company, "name", "名称なし")
                or "名称なし"
            ).strip()

            if company_code:
                company_label = (
                    f"{company_code} - {company_name}"
                )
            else:
                company_label = company_name

            company_map[company_label] = {
                "id": company_id,
                "name": company_name,
            }

        selected_company_label = st.selectbox(
            "荷主（番号・名称で検索）",
            options=list(company_map.keys()),
            key="pallet_selected_company",
            on_change=company_changed,
        )
        selected_company = company_map[selected_company_label]
        selected_company_id = selected_company["id"]
        selected_company_name = selected_company["name"]

        items = get_items_for_company(
            conn=conn,
            company_id=selected_company_id,
        )

        if not items:
            st.warning(
                "登録可能な商品がありません。"
                "案件管理・商品管理から商品を登録してください。"
            )

        else:
            item_map = {}

            for item in items:
                item_code = row_value(item, "code", "")
                item_name = row_value(item, "name", "")
                project_code = row_value(item, "project_code", "")
                project_name = row_value(item, "project_name", "")

                item_label = (
                    f"{item_code} - {item_name}"
                    f"　［{project_code} - {project_name}］"
                )
                item_map[item_label] = item

            selected_item_label = st.selectbox(
                "商品",
                options=list(item_map.keys()),
                key="pallet_selected_item",
                on_change=clear_preview,
            )
            selected_item = item_map[selected_item_label]

            selected_item_id = row_value(selected_item, "id")
            selected_item_code = row_value(
                selected_item,
                "code",
                "",
            )
            selected_item_name = row_value(
                selected_item,
                "name",
                "",
            )
            selected_project_id = row_value(
                selected_item,
                "project_id",
            )
            selected_project_name = row_value(
                selected_item,
                "project_name",
                "",
            )

            info_col1, info_col2, info_col3 = st.columns(3)

            with info_col1:
                st.metric("荷主", selected_company_name)

            with info_col2:
                st.metric(
                    "商品コード",
                    selected_item_code or "未設定",
                )

            with info_col3:
                st.metric(
                    "案件",
                    selected_project_name or "案件なし",
                )

            st.divider()

            input_col1, input_col2 = st.columns(2)

            with input_col1:
                received_qty = st.number_input(
                    "入庫個数",
                    min_value=1,
                    max_value=1_000_000,
                    step=1,
                    key="pallet_received_qty",
                    on_change=clear_preview,
                )

            with input_col2:
                pallet_count = st.number_input(
                    "パレット枚数",
                    min_value=1,
                    max_value=1_000,
                    step=1,
                    key="pallet_count",
                    on_change=clear_preview,
                )

            received_qty = int(received_qty)
            pallet_count = int(pallet_count)

            allocation_mode = st.radio(
                "数量の割り振り方法",
                options=["自動", "手動"],
                horizontal=True,
                key="pallet_allocation_mode",
                on_change=clear_preview,
            )

            if pallet_count > received_qty:
                st.warning(
                    "パレット枚数が入庫個数を上回っています。"
                    "数量0個のパレットが発生します。"
                )

            if st.button(
                "パレット割り振りを作成",
                type="primary",
                use_container_width=True,
            ):
                st.session_state.pallet_editor_version += 1
                st.session_state.pallet_preview = {
                    "company_id": selected_company_id,
                    "company_name": selected_company_name,
                    "project_id": selected_project_id,
                    "project_name": selected_project_name,
                    "item_id": selected_item_id,
                    "item_code": selected_item_code,
                    "item_name": selected_item_name,
                    "received_qty": received_qty,
                    "pallet_count": pallet_count,
                    "allocation_mode": allocation_mode,
                    "allocations": create_auto_allocation(
                        received_qty=received_qty,
                        pallet_count=pallet_count,
                    ),
                }

            preview = st.session_state.pallet_preview

            if preview is not None:
                same_selection = (
                    preview["company_id"] == selected_company_id
                    and preview["item_id"] == selected_item_id
                    and preview["received_qty"] == received_qty
                    and preview["pallet_count"] == pallet_count
                    and preview["allocation_mode"] == allocation_mode
                )

                if not same_selection:
                    st.warning(
                        "入力内容が変更されています。"
                        "もう一度「パレット割り振りを作成」"
                        "を押してください。"
                    )

                else:
                    st.divider()
                    st.subheader("パレット割り振りプレビュー")

                    st.write(
                        f"**荷主：** {preview['company_name']}　"
                        f"**商品：** {preview['item_name']}　"
                        f"**入庫個数：** "
                        f"{preview['received_qty']:,}個　"
                        f"**パレット枚数：** "
                        f"{preview['pallet_count']:,}枚"
                    )

                    allocation_df = pd.DataFrame(
                        preview["allocations"]
                    )

                    if allocation_mode == "自動":
                        edited_df = allocation_df.copy()

                        st.dataframe(
                            edited_df,
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "パレット番号":
                                    st.column_config.TextColumn(
                                        "パレット番号",
                                        disabled=True,
                                    ),
                                "個数":
                                    st.column_config.NumberColumn(
                                        "個数",
                                        min_value=0,
                                        step=1,
                                        format="%d 個",
                                    ),
                            },
                        )

                    else:
                        st.caption(
                            "個数欄を直接変更できます。"
                            "パレット番号は変更できません。"
                        )

                        editor_key = (
                            "manual_pallet_allocation_editor_"
                            f"{st.session_state.pallet_editor_version}"
                        )

                        edited_df = st.data_editor(
                            allocation_df,
                            hide_index=True,
                            use_container_width=True,
                            num_rows="fixed",
                            key=editor_key,
                            column_config={
                                "パレット番号":
                                    st.column_config.TextColumn(
                                        "パレット番号",
                                        disabled=True,
                                    ),
                                "個数":
                                    st.column_config.NumberColumn(
                                        "個数",
                                        min_value=0,
                                        max_value=1_000_000,
                                        step=1,
                                        required=True,
                                        format="%d 個",
                                    ),
                            },
                        )

                    edited_df["個数"] = (
                        pd.to_numeric(
                            edited_df["個数"],
                            errors="coerce",
                        )
                        .fillna(0)
                        .astype(int)
                    )

                    allocated_total = int(edited_df["個数"].sum())
                    difference = received_qty - allocated_total

                    result_col1, result_col2, result_col3 = (
                        st.columns(3)
                    )

                    with result_col1:
                        st.metric(
                            "入庫個数",
                            f"{received_qty:,} 個",
                        )

                    with result_col2:
                        st.metric(
                            "割り振り合計",
                            f"{allocated_total:,} 個",
                        )

                    with result_col3:
                        st.metric(
                            "差分",
                            f"{difference:,} 個",
                        )

                    if allocated_total == received_qty:
                        st.success(
                            "入庫個数とパレット割り振りの"
                            "合計が一致しています。"
                        )

                        current_allocations = edited_df.to_dict(
                            "records"
                        )
                        st.session_state.pallet_preview[
                            "allocations"
                        ] = current_allocations

                        if st.button(
                            "💾 パレット登録",
                            type="primary",
                            use_container_width=True,
                        ):
                            try:
                                batch_code = create_pallet_batch(
                                    conn=conn,
                                    company_id=preview["company_id"],
                                    project_id=preview["project_id"],
                                    item_id=preview["item_id"],
                                    allocations=current_allocations,
                                    username=username,
                                )

                                registered_pallets = (
                                    get_batch_pallets(
                                        conn=conn,
                                        batch_code=batch_code,
                                    )
                                )
                                pdf_data = create_pallet_a4_pdf(
                                    registered_pallets
                                )

                                st.session_state.pallet_last_pdf = (
                                    pdf_data
                                )
                                st.session_state.pallet_last_pdf_name = (
                                    f"pallet_{batch_code}.pdf"
                                )
                                st.session_state.pallet_last_batch_code = (
                                    batch_code
                                )
                                st.session_state.pallet_receiving_flash = (
                                    "登録が完了しました！"
                                    f" 登録No：{batch_code}"
                                )
                                st.session_state.pallet_preview = None
                                st.session_state.pallet_reset_inputs = True
                                st.rerun()

                            except PalletError as exc:
                                st.error(str(exc))

                            except Exception as exc:
                                st.error(
                                    "パレット登録中にエラーが"
                                    f"発生しました：{exc}"
                                )

                    elif allocated_total < received_qty:
                        st.error(
                            f"割り振りが {difference:,} 個不足しています。"
                        )

                    else:
                        st.error(
                            f"割り振りが "
                            f"{abs(difference):,} 個多くなっています。"
                        )


# ============================================================
# QR出庫
# ============================================================
with tab_shipping:
    st.subheader("QR出庫")

    st.info(
        "A4パレット票のQRコードを読み取ります。"
        "バーコードリーダーのEnter送信で検索できます。"
    )

    if st.session_state.pallet_shipping_flash:
        st.success(st.session_state.pallet_shipping_flash)
        st.session_state.pallet_shipping_flash = ""

    with st.form(
        "pallet_qr_scan_form",
        clear_on_submit=True,
    ):
        scanned_code = st.text_input(
            "パレットQRコード",
            placeholder="PAL000000001",
        )

        scan_submitted = st.form_submit_button(
            "QRコードを読み取る",
            type="primary",
            use_container_width=True,
        )

    if scan_submitted:
        normalized_code = scanned_code.strip().upper()

        if not normalized_code:
            st.warning("QRコードを読み取ってください。")
        else:
            pallet = get_pallet_by_code(
                conn=conn,
                pallet_code=normalized_code,
            )

            if pallet is None:
                st.session_state.pallet_shipping_target = None
                st.error(
                    f"パレット「{normalized_code}」"
                    "が見つかりません。"
                )
            else:
                st.session_state.pallet_shipping_target = normalized_code

    target_code = st.session_state.pallet_shipping_target

    if target_code:
        pallet = get_pallet_by_code(
            conn=conn,
            pallet_code=target_code,
        )

        if pallet is None:
            st.session_state.pallet_shipping_target = None
            st.error("対象パレットが見つかりません。")

        else:
            st.divider()
            st.subheader("出庫対象")

            pallet_number = (
                f"{int(row_value(pallet, 'pallet_sequence', 0)):03d}"
                f" / "
                f"{int(row_value(pallet, 'total_pallets', 0)):03d}"
            )

            target_col1, target_col2, target_col3 = st.columns(3)

            with target_col1:
                st.metric(
                    "パレット番号",
                    pallet_number,
                )

            with target_col2:
                st.metric(
                    "商品",
                    row_value(pallet, "item_name", ""),
                )

            with target_col3:
                st.metric(
                    "現在庫",
                    f"{int(row_value(pallet, 'current_qty', 0)):,} 個",
                )

            st.write(
                f"**パレットコード：** "
                f"{row_value(pallet, 'pallet_code', '')}　"
                f"**荷主：** "
                f"{row_value(pallet, 'company_name', '')}　"
                f"**状態：** "
                f"{row_value(pallet, 'status', '')}"
            )

            current_qty = int(row_value(pallet, "current_qty", 0))

            if current_qty <= 0:
                st.warning("このパレットはすでに出庫済みです。")

                if st.button(
                    "読み取りを解除",
                    use_container_width=True,
                ):
                    st.session_state.pallet_shipping_target = None
                    st.rerun()

            else:
                with st.form("pallet_shipping_form"):
                    shipping_qty = st.number_input(
                        "出庫数量",
                        min_value=1,
                        max_value=current_qty,
                        value=current_qty,
                        step=1,
                    )

                    shipping_remarks = st.text_input(
                        "備考（任意）",
                        placeholder="出庫先・伝票番号など",
                    )

                    ship_submitted = st.form_submit_button(
                        "📦 出庫を確定",
                        type="primary",
                        use_container_width=True,
                    )

                if ship_submitted:
                    try:
                        result = ship_pallet(
                            conn=conn,
                            pallet_code=target_code,
                            quantity=int(shipping_qty),
                            username=username,
                            remarks=shipping_remarks,
                        )

                        st.session_state.pallet_shipping_flash = (
                            f"{result['pallet_code']} から "
                            f"{result['shipped_qty']:,} 個を"
                            "出庫しました。"
                            f" 残り {result['after_qty']:,} 個"
                        )
                        st.session_state.pallet_shipping_target = None
                        st.rerun()

                    except PalletError as exc:
                        st.error(str(exc))

                    except Exception as exc:
                        st.error(
                            "出庫処理中にエラーが"
                            f"発生しました：{exc}"
                        )


# ============================================================
# 在庫確認
# ============================================================
with tab_stock:
    st.subheader("在庫確認")

    filter_col1, filter_col2 = st.columns([1, 2])

    with filter_col1:
        stock_status = st.selectbox(
            "状態",
            options=["保管中", "出庫済み", "すべて"],
            key="pallet_stock_status",
        )

    with filter_col2:
        stock_search = st.text_input(
            "検索",
            placeholder=(
                "パレットコード・荷主・案件・商品・保管場所"
            ),
            key="pallet_stock_search",
        )

    stock_rows = list_pallet_stock(
        conn=conn,
        status=stock_status,
        search_text=stock_search,
    )

    total_current_qty = sum(
        int(row_value(row, "current_qty", 0))
        for row in stock_rows
    )
    storage_pallets = sum(
        1
        for row in stock_rows
        if int(row_value(row, "current_qty", 0)) > 0
    )

    stock_metric1, stock_metric2 = st.columns(2)

    with stock_metric1:
        st.metric(
            "表示パレット数",
            f"{len(stock_rows):,} 枚",
        )

    with stock_metric2:
        st.metric(
            "表示中の現在庫",
            f"{total_current_qty:,} 個",
            help=f"在庫が残っているパレット：{storage_pallets:,}枚",
        )

    if not stock_rows:
        st.info("条件に一致するパレットはありません。")

    else:
        stock_df = stock_dataframe(stock_rows)

        st.dataframe(
            stock_df,
            hide_index=True,
            use_container_width=True,
        )

        csv_data = stock_df.to_csv(
            index=False,
        ).encode("utf-8-sig")

        st.download_button(
            "在庫一覧CSVをダウンロード",
            data=csv_data,
            file_name="pallet_stock.csv",
            mime="text/csv",
            use_container_width=True,
        )

        printable_rows = [
            row
            for row in stock_rows
            if int(row_value(row, "current_qty", 0)) > 0
        ]

        if printable_rows:
            st.divider()
            st.subheader("A4票の再出力")

            printable_map = {
                (
                    f"{row_value(row, 'pallet_code', '')} - "
                    f"{row_value(row, 'item_name', '')} "
                    f"({int(row_value(row, 'current_qty', 0)):,}個)"
                ): row
                for row in printable_rows
            }

            selected_print_label = st.selectbox(
                "再出力するパレット",
                options=list(printable_map.keys()),
                key="pallet_reprint_target",
            )
            selected_print_row = printable_map[
                selected_print_label
            ]
            reprint_pdf = create_pallet_a4_pdf(
                [selected_print_row]
            )
            reprint_code = row_value(
                selected_print_row,
                "pallet_code",
                "pallet",
            )

            st.download_button(
                "📄 このパレットのA4票をダウンロード",
                data=reprint_pdf,
                file_name=f"{reprint_code}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )


# ============================================================
# 入出庫履歴
# ============================================================
with tab_history:
    st.subheader("入出庫履歴")

    if st.session_state.pallet_history_flash:
        st.success(st.session_state.pallet_history_flash)
        st.session_state.pallet_history_flash = ""

    if st.session_state.pallet_history_pdf is not None:
        st.download_button(
            "📄 修正後のA4票をダウンロード",
            data=st.session_state.pallet_history_pdf,
            file_name=st.session_state.pallet_history_pdf_name,
            mime="application/pdf",
            use_container_width=True,
        )
        st.caption(
            "修正した登録No："
            f"{st.session_state.pallet_history_pdf_batch_code}"
        )

    history_filter_col1, history_filter_col2 = st.columns(2)

    with history_filter_col1:
        history_type = st.selectbox(
            "区分",
            options=["すべて", "入庫", "出庫"],
            key="pallet_history_type",
        )

    with history_filter_col2:
        history_limit = st.number_input(
            "表示件数",
            min_value=10,
            max_value=5_000,
            value=500,
            step=10,
            key="pallet_history_limit",
        )

    history_rows = list_pallet_history(
        conn=conn,
        history_type=history_type,
        limit=int(history_limit),
    )

    if not history_rows:
        st.info("入出庫履歴はありません。")

    else:
        history_df = history_dataframe(history_rows)

        st.dataframe(
            history_df,
            hide_index=True,
            use_container_width=True,
        )

        history_csv = history_df.to_csv(
            index=False,
        ).encode("utf-8-sig")

        st.download_button(
            "履歴CSVをダウンロード",
            data=history_csv,
            file_name="pallet_history.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()
    st.subheader("誤登録の変更・削除")
    st.caption(
        "まだ一度も出庫していない登録Noだけ変更・削除できます。"
        "パレットコードとパレット枚数は変更されません。"
    )

    editable_batches = list_editable_pallet_batches(conn)

    if not editable_batches:
        st.info("変更・削除できる未出庫の登録はありません。")

    else:
        editable_batch_map = {}

        for batch in editable_batches:
            batch_code = str(
                row_value(batch, "batch_code", "")
            )
            company_name = str(
                row_value(batch, "company_name", "")
            )
            item_code = str(
                row_value(batch, "item_code", "")
            )
            item_name = str(
                row_value(batch, "item_name", "")
            )
            total_qty = int(
                row_value(batch, "total_qty", 0)
            )
            pallet_count = int(
                row_value(batch, "pallet_count", 0)
            )

            label = (
                f"{batch_code}｜{company_name}｜"
                f"{item_code} {item_name}｜"
                f"{total_qty:,}個・{pallet_count:,}枚"
            )
            editable_batch_map[label] = batch

        selected_edit_label = st.selectbox(
            "変更・削除する登録No",
            options=list(editable_batch_map.keys()),
            key="pallet_edit_batch",
        )
        selected_edit_batch = editable_batch_map[
            selected_edit_label
        ]
        selected_edit_batch_code = str(
            row_value(
                selected_edit_batch,
                "batch_code",
                "",
            )
        )
        edit_pallets = get_batch_pallets(
            conn,
            selected_edit_batch_code,
        )

        if not edit_pallets:
            st.warning(
                "選択した登録Noは別の端末で更新されました。"
                "画面を更新してください。"
            )

        else:
            current_company_id = row_value(
                edit_pallets[0],
                "company_id",
            )
            current_item_id = row_value(
                edit_pallets[0],
                "item_id",
            )

            edit_companies = conn.execute(
                """
                SELECT
                    id,
                    code,
                    name
                FROM companies
                ORDER BY
                    code,
                    name
                """
            ).fetchall()
            edit_company_map = {}

            for company in edit_companies:
                company_id = row_value(company, "id")
                company_code = str(
                    row_value(company, "code", "") or ""
                ).strip()
                company_name = str(
                    row_value(company, "name", "名称なし")
                    or "名称なし"
                ).strip()
                company_label = (
                    f"{company_code} - {company_name}"
                    if company_code
                    else company_name
                )
                edit_company_map[company_label] = company

            edit_company_labels = list(edit_company_map.keys())
            current_company_index = next(
                (
                    index
                    for index, label in enumerate(edit_company_labels)
                    if row_value(
                        edit_company_map[label],
                        "id",
                    )
                    == current_company_id
                ),
                0,
            )

            edit_items = get_items_for_company(
                conn,
                current_company_id,
            )
            edit_item_map = {}

            for item in edit_items:
                item_code = row_value(item, "code", "")
                item_name = row_value(item, "name", "")
                project_code = row_value(
                    item,
                    "project_code",
                    "",
                )
                project_name = row_value(
                    item,
                    "project_name",
                    "",
                )
                item_label = (
                    f"{item_code} - {item_name}"
                    f"　［{project_code} - {project_name}］"
                )
                edit_item_map[item_label] = item

            edit_item_labels = list(edit_item_map.keys())
            current_item_index = next(
                (
                    index
                    for index, label in enumerate(edit_item_labels)
                    if row_value(
                        edit_item_map[label],
                        "id",
                    )
                    == current_item_id
                ),
                0,
            )

            with st.form(
                f"pallet_batch_edit_form_"
                f"{selected_edit_batch_code}"
            ):
                edit_company_label = st.selectbox(
                    "荷主",
                    options=edit_company_labels,
                    index=current_company_index,
                )
                edit_item_label = st.selectbox(
                    "商品",
                    options=edit_item_labels,
                    index=current_item_index,
                )

                edit_allocation_rows = []

                for pallet in edit_pallets:
                    sequence = int(
                        row_value(
                            pallet,
                            "pallet_sequence",
                            0,
                        )
                    )
                    total_pallets = int(
                        row_value(
                            pallet,
                            "total_pallets",
                            0,
                        )
                    )
                    edit_allocation_rows.append(
                        {
                            "パレットコード": row_value(
                                pallet,
                                "pallet_code",
                                "",
                            ),
                            "パレット番号": (
                                f"{sequence:03d} / "
                                f"{total_pallets:03d}"
                            ),
                            "個数": int(
                                row_value(
                                    pallet,
                                    "initial_qty",
                                    0,
                                )
                            ),
                        }
                    )

                edited_allocations = st.data_editor(
                    pd.DataFrame(edit_allocation_rows),
                    hide_index=True,
                    use_container_width=True,
                    num_rows="fixed",
                    key=(
                        "pallet_batch_edit_allocations_"
                        f"{selected_edit_batch_code}"
                    ),
                    column_config={
                        "パレットコード":
                            st.column_config.TextColumn(
                                "パレットコード",
                                disabled=True,
                            ),
                        "パレット番号":
                            st.column_config.TextColumn(
                                "パレット番号",
                                disabled=True,
                            ),
                        "個数":
                            st.column_config.NumberColumn(
                                "商品数",
                                min_value=0,
                                max_value=1_000_000,
                                step=1,
                                required=True,
                                format="%d 個",
                            ),
                    },
                )

                edited_allocations["個数"] = (
                    pd.to_numeric(
                        edited_allocations["個数"],
                        errors="coerce",
                    )
                    .fillna(0)
                    .astype(int)
                )
                edited_total_qty = int(
                    edited_allocations["個数"].sum()
                )
                st.metric(
                    "変更後の商品総数",
                    f"{edited_total_qty:,} 個",
                )

                update_submitted = st.form_submit_button(
                    "💾 変更を保存",
                    type="primary",
                    use_container_width=True,
                )

            if update_submitted:
                try:
                    selected_edit_company = edit_company_map[
                        edit_company_label
                    ]
                    selected_edit_item = edit_item_map[
                        edit_item_label
                    ]

                    result = update_pallet_batch(
                        conn=conn,
                        batch_code=selected_edit_batch_code,
                        company_id=row_value(
                            selected_edit_company,
                            "id",
                        ),
                        project_id=row_value(
                            selected_edit_item,
                            "project_id",
                        ),
                        item_id=row_value(
                            selected_edit_item,
                            "id",
                        ),
                        allocations=edited_allocations.to_dict(
                            "records"
                        ),
                    )

                    revised_pallets = get_batch_pallets(
                        conn,
                        selected_edit_batch_code,
                    )
                    revised_pdf = create_pallet_a4_pdf(
                        revised_pallets
                    )
                    st.session_state.pallet_history_pdf = (
                        revised_pdf
                    )
                    st.session_state.pallet_history_pdf_name = (
                        f"pallet_{selected_edit_batch_code}.pdf"
                    )
                    st.session_state[
                        "pallet_history_pdf_batch_code"
                    ] = selected_edit_batch_code
                    st.session_state.pallet_history_flash = (
                        "登録内容を変更しました！"
                        f" 商品総数："
                        f"{int(result['total_qty']):,}個"
                    )
                    st.rerun()

                except PalletError as exc:
                    st.error(str(exc))

                except Exception as exc:
                    st.error(
                        "登録内容の変更中にエラーが"
                        f"発生しました：{exc}"
                    )

            with st.expander("🗑 この誤登録を削除"):
                st.warning(
                    "この登録Noに含まれる全パレットを、"
                    "在庫一覧と入出庫履歴から削除します。"
                )

                with st.form(
                    f"pallet_batch_delete_form_"
                    f"{selected_edit_batch_code}"
                ):
                    delete_confirmed = st.checkbox(
                        f"{selected_edit_batch_code} を削除する"
                    )
                    delete_submitted = st.form_submit_button(
                        "🗑 登録を削除",
                        use_container_width=True,
                    )

                if delete_submitted:
                    if not delete_confirmed:
                        st.warning(
                            "削除確認にチェックを入れてから、"
                            "もう一度「登録を削除」を押してください。"
                        )

                    else:
                        try:
                            result = delete_pallet_batch(
                                conn,
                                selected_edit_batch_code,
                            )
                            st.session_state.pallet_history_pdf = None
                            st.session_state.pallet_history_pdf_name = ""
                            st.session_state[
                                "pallet_history_pdf_batch_code"
                            ] = ""
                            st.session_state.pallet_history_flash = (
                                "誤登録を削除しました。"
                                f" 商品総数："
                                f"{int(result['total_qty']):,}個"
                            )
                            st.rerun()

                        except PalletError as exc:
                            st.error(str(exc))

                        except Exception as exc:
                            st.error(
                                "誤登録の削除中にエラーが"
                                f"発生しました：{exc}"
                            )


# ============================================================
# DB切断
# ============================================================
conn.close()
