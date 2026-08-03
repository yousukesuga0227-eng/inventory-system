from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from auth import check_login
from database import get_connection
from pages.pallet.pallet_db import (
    PalletError,
    cancel_receiving_plan,
    confirm_receiving_plan,
    create_receiving_plan,
    delete_pallet_batch,
    get_batch_pallets,
    get_pallet_by_code,
    get_receiving_plan_by_code,
    list_editable_pallet_batches,
    list_pallet_categories,
    list_pallet_history,
    list_pallet_stock,
    list_receiving_plans,
    ship_pallet,
    update_pallet_batch,
)
from pages.pallet.pallet_documents import (
    create_pallet_a4_pdf,
    create_receiving_plan_a4_pdf,
)
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
        "SHARKを停止して INSTALL_PALLET_CATEGORY_NUMBERING.py を"
        "一度だけ実行し、再起動してください。"
    )
    conn.close()
    st.stop()


# ============================================================
# 基本表示・セッション
# ============================================================
st.title("在庫品・パレット管理")

display_name = st.session_state.get(
    "display_name",
    st.session_state.get("username", ""),
)
username = st.session_state.get("username", display_name)

st.caption(f"ログイン中：{display_name}")
st.info(
    "基本の流れ：必要事項を入力 → A4を印刷 → QRを読み取る → 入庫完了"
)

SESSION_DEFAULTS = {
    "simple_plan_pdf": None,
    "simple_plan_pdf_name": "",
    "simple_plan_code": "",
    "simple_plan_flash": "",
    "simple_plan_form_version": 0,
    "simple_qr_flash": "",
    "simple_admin_flash": "",
    "simple_admin_pdf": None,
    "simple_admin_pdf_name": "",
}

for session_key, default_value in SESSION_DEFAULTS.items():
    if session_key not in st.session_state:
        st.session_state[session_key] = default_value


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


def format_date(value):
    if isinstance(value, datetime):
        value = value.date()

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    return str(value or "")[:10]


def company_option_map(companies):
    options = {}

    for company in companies:
        company_id = int(row_value(company, "id", 0))
        company_code = str(row_value(company, "code", "") or "").strip()
        company_name = str(
            row_value(company, "name", "名称なし") or "名称なし"
        ).strip()
        label = (
            f"{company_code} - {company_name}"
            if company_code
            else company_name
        )
        options[label] = {
            "id": company_id,
            "code": company_code,
            "name": company_name,
        }

    return options


def management_number(row):
    start_number = int(row_value(row, "category_start_sequence", 0))
    end_number = int(
        row_value(row, "category_end_sequence", start_number)
    )

    if end_number != start_number:
        return f"{start_number:03d}～{end_number:03d}"

    return f"{start_number:03d}"


def stock_dataframe(rows):
    return pd.DataFrame(
        [
            {
                "入庫日時": format_datetime(
                    row_value(row, "created_at", "")
                ),
                "管理番号": (
                    f"{int(row_value(row, 'category_sequence', 0)):03d}"
                ),
                "業者名": row_value(row, "company_name", ""),
                "大カテゴリー": row_value(row, "category_name", ""),
                "商品コード": row_value(row, "item_code", ""),
                "商品名": row_value(row, "item_name", ""),
                "現在庫": int(row_value(row, "current_qty", 0)),
                "状態": row_value(row, "status", ""),
                "入庫担当者": row_value(row, "created_by", ""),
            }
            for row in rows
        ]
    )


def history_dataframe(rows):
    return pd.DataFrame(
        [
            {
                "日時": format_datetime(
                    row_value(row, "created_at", "")
                ),
                "区分": row_value(row, "history_type", ""),
                "管理番号": management_number(row),
                "業者名": row_value(row, "company_name", ""),
                "大カテゴリー": row_value(row, "category_name", ""),
                "商品コード": row_value(row, "item_code", ""),
                "商品名": row_value(row, "item_name", ""),
                "商品数": int(row_value(row, "qty", 0)),
                "処理後在庫": int(row_value(row, "after_qty", 0)),
                "担当者": row_value(row, "username", ""),
            }
            for row in rows
        ]
    )


def pending_plan_label(plan):
    return (
        f"{row_value(plan, 'receipt_code', '')}｜"
        f"{row_value(plan, 'company_name', '')}｜"
        f"{row_value(plan, 'category_name', '')}｜"
        f"{row_value(plan, 'item_name', '')}"
    )


def editable_batch_label(batch):
    return (
        f"{row_value(batch, 'batch_code', '')}｜"
        f"{row_value(batch, 'category_name', '未分類')}｜"
        f"{row_value(batch, 'company_name', '')}｜"
        f"{row_value(batch, 'item_name', '')}｜"
        f"{int(row_value(batch, 'total_qty', 0)):,}個"
    )


# ============================================================
# 4つだけの通常画面
# ============================================================
tab_register, tab_qr, tab_stock, tab_history = st.tabs(
    ["登録・印刷", "QR入出庫", "現在庫", "履歴"]
)


# ============================================================
# 1. 登録・印刷
# ============================================================
with tab_register:
    st.subheader("必要事項を入力してA4を作成")
    st.caption(
        "登録すると大カテゴリー別の番号とQRを正式発行します。"
        "A4は必ず1パレットにつき1ページです。"
    )

    if st.session_state.simple_plan_flash:
        st.success(st.session_state.simple_plan_flash)
        st.session_state.simple_plan_flash = ""

    if st.session_state.simple_plan_pdf is not None:
        st.download_button(
            "📄 A4管理票を開く・ダウンロード",
            data=st.session_state.simple_plan_pdf,
            file_name=st.session_state.simple_plan_pdf_name,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
        st.caption(
            "PDFを開いて Ctrl＋P で印刷してください。 "
            f"入庫管理番号：{st.session_state.simple_plan_code}"
        )

    companies = conn.execute(
        """
        SELECT id, code, name
        FROM companies
        ORDER BY code, name
        """
    ).fetchall()
    company_map = company_option_map(companies)

    active_categories = list_pallet_categories(
        conn,
        include_hidden=False,
    )
    category_map = {
        str(row_value(category, "name", "")): int(
            row_value(category, "id", 0)
        )
        for category in active_categories
        if str(row_value(category, "name", "")).strip()
    }

    if not company_map:
        st.warning("企業マスターに業者が登録されていません。")
    elif not category_map:
        st.warning(
            "大カテゴリーがありません。DB導入スクリプトを確認してください。"
        )
    else:
        form_version = st.session_state.simple_plan_form_version

        with st.form(f"simple_receiving_plan_form_{form_version}"):
            top_col1, top_col2 = st.columns(2)

            with top_col1:
                receiving_date_value = st.date_input(
                    "入庫予定日",
                    value=date.today(),
                )

            with top_col2:
                company_label = st.selectbox(
                    "業者名",
                    options=list(company_map.keys()),
                )

            category_col1, category_col2 = st.columns(2)

            with category_col1:
                category_label = st.selectbox(
                    "大カテゴリー",
                    options=list(category_map.keys()),
                )

            with category_col2:
                new_category_name = st.text_input(
                    "新しい大カテゴリー（新規のときだけ）",
                    value="",
                )

            item_col1, item_col2 = st.columns(2)

            with item_col1:
                item_code_value = st.text_input(
                    "商品コード（任意）",
                    value="",
                )

            with item_col2:
                item_name_value = st.text_input(
                    "商品名",
                    value="",
                )

            qty_col1, qty_col2 = st.columns(2)

            with qty_col1:
                pallet_count = st.number_input(
                    "パレット枚数",
                    min_value=1,
                    max_value=1000,
                    value=1,
                    step=1,
                )

            with qty_col2:
                qty_per_pallet = st.number_input(
                    "1パレットの商品数",
                    min_value=1,
                    max_value=1_000_000,
                    value=1,
                    step=1,
                )

            total_qty = int(pallet_count) * int(qty_per_pallet)
            st.caption(
                f"商品総数：{total_qty:,}個 ／ "
                f"A4：{int(pallet_count):,}ページ"
            )

            register_submitted = st.form_submit_button(
                "登録してA4を作成",
                type="primary",
                use_container_width=True,
            )

        if register_submitted:
            normalized_item_name = str(item_name_value or "").strip()
            normalized_new_category = str(
                new_category_name or ""
            ).strip()

            if not normalized_item_name:
                st.error("商品名を入力してください。")
            else:
                selected_company = company_map[company_label]

                if normalized_new_category:
                    selected_category_id = None
                    selected_category_name = normalized_new_category
                else:
                    selected_category_id = category_map[category_label]
                    selected_category_name = category_label

                try:
                    receipt_code = create_receiving_plan(
                        conn=conn,
                        receiving_date=receiving_date_value,
                        company_id=selected_company["id"],
                        project_id=None,
                        item_id=None,
                        pallet_count=int(pallet_count),
                        qty_per_pallet=int(qty_per_pallet),
                        username=username,
                        item_code=str(item_code_value or "").strip(),
                        item_name=normalized_item_name,
                        category_id=selected_category_id,
                        category_name=selected_category_name,
                    )
                    registered_plan = get_receiving_plan_by_code(
                        conn,
                        receipt_code,
                    )
                    pdf_data = create_receiving_plan_a4_pdf(
                        [registered_plan]
                    )
                    st.session_state.simple_plan_pdf = pdf_data
                    st.session_state.simple_plan_pdf_name = (
                        f"receiving_{receipt_code}.pdf"
                    )
                    st.session_state.simple_plan_code = receipt_code
                    st.session_state.simple_plan_flash = (
                        "登録できました！ "
                        f"A4は{int(pallet_count):,}ページです。"
                    )
                    st.session_state.simple_plan_form_version += 1
                    st.rerun()

                except PalletError as exc:
                    st.error(str(exc))

                except Exception as exc:
                    st.error(
                        "登録中にエラーが発生しました："
                        f"{exc}"
                    )


# ============================================================
# 2. QR入出庫
# ============================================================
with tab_qr:
    st.subheader("QRを読んで入出庫")
    st.caption(
        "バーコードリーダーのEnter送信でそのまま確定します。"
        "通常の出庫は1パレット全数です。"
    )

    if st.session_state.simple_qr_flash:
        st.success(st.session_state.simple_qr_flash)
        st.session_state.simple_qr_flash = ""

    operation_type = st.radio(
        "処理",
        options=["入庫", "出庫"],
        horizontal=True,
        key="simple_qr_operation",
    )

    with st.form("simple_qr_form", clear_on_submit=True):
        if operation_type == "出庫":
            shipping_quantity_text = st.text_input(
                "出庫数（空欄ならパレット全数）",
                value="",
                placeholder="分納するときだけ入力",
            )
        else:
            shipping_quantity_text = ""

        qr_code = st.text_input(
            "QRコード",
            value="",
            placeholder="QRを読み取ってEnter",
        )
        qr_submitted = st.form_submit_button(
            f"読み取って{operation_type}",
            type="primary",
            use_container_width=True,
        )

    if qr_submitted:
        normalized_code = str(qr_code or "").strip().upper()

        if not normalized_code:
            st.warning("QRコードを読み取ってください。")

        elif operation_type == "入庫":
            try:
                result = confirm_receiving_plan(
                    conn=conn,
                    receipt_or_pallet_code=normalized_code,
                    username=username,
                )
                st.session_state.simple_qr_flash = (
                    f"{result['receipt_code']} を入庫しました。 "
                    f"商品総数：{int(result['total_qty']):,}個"
                )
                st.rerun()

            except PalletError as exc:
                st.error(str(exc))

            except Exception as exc:
                st.error(f"入庫中にエラーが発生しました：{exc}")

        else:
            try:
                pallet = get_pallet_by_code(
                    conn=conn,
                    pallet_code=normalized_code,
                )

                if pallet is None:
                    raise PalletError(
                        "パレットが見つかりません。QRを確認してください。"
                    )

                current_qty = int(row_value(pallet, "current_qty", 0))
                quantity_text = str(
                    shipping_quantity_text or ""
                ).replace(",", "").strip()

                if quantity_text:
                    try:
                        shipping_qty = int(quantity_text)
                    except ValueError as exc:
                        raise PalletError(
                            "出庫数は整数で入力してください。"
                        ) from exc
                else:
                    shipping_qty = current_qty

                result = ship_pallet(
                    conn=conn,
                    pallet_code=normalized_code,
                    quantity=shipping_qty,
                    username=username,
                )
                st.session_state.simple_qr_flash = (
                    f"管理番号 "
                    f"{int(row_value(pallet, 'category_sequence', 0)):03d} "
                    f"を{int(result['shipped_qty']):,}個出庫しました。 "
                    f"残り：{int(result['after_qty']):,}個"
                )
                st.rerun()

            except PalletError as exc:
                st.error(str(exc))

            except Exception as exc:
                st.error(f"出庫中にエラーが発生しました：{exc}")


# ============================================================
# 3. 現在庫
# ============================================================
with tab_stock:
    st.subheader("現在庫")

    stock_search = st.text_input(
        "検索",
        placeholder="業者名・大カテゴリー・商品名・管理番号",
        key="simple_stock_search",
    )
    stock_rows = list_pallet_stock(
        conn=conn,
        status="保管中",
        search_text=stock_search,
    )
    total_current_qty = sum(
        int(row_value(row, "current_qty", 0))
        for row in stock_rows
    )

    metric_col1, metric_col2 = st.columns(2)

    with metric_col1:
        st.metric("保管中パレット", f"{len(stock_rows):,}枚")

    with metric_col2:
        st.metric("現在庫合計", f"{total_current_qty:,}個")

    if not stock_rows:
        st.info("現在庫はありません。")
    else:
        st.dataframe(
            stock_dataframe(stock_rows),
            hide_index=True,
            use_container_width=True,
        )


# ============================================================
# 4. 履歴
# ============================================================
with tab_history:
    st.subheader("入出庫履歴")

    history_type = st.radio(
        "表示",
        options=["すべて", "入庫", "出庫"],
        horizontal=True,
        key="simple_history_type",
    )
    history_rows = list_pallet_history(
        conn=conn,
        history_type=history_type,
        limit=1000,
    )

    if not history_rows:
        st.info("入出庫履歴はありません。")
    else:
        st.dataframe(
            history_dataframe(history_rows),
            hide_index=True,
            use_container_width=True,
        )

    st.divider()

    # 通常作業から修正・削除を隔離する。
    with st.expander("管理メニュー（再印刷・取消・誤登録修正）"):
        st.caption(
            "通常の入出庫では使いません。誤登録のときだけ開いてください。"
        )

        if st.session_state.simple_admin_flash:
            st.success(st.session_state.simple_admin_flash)
            st.session_state.simple_admin_flash = ""

        if st.session_state.simple_admin_pdf is not None:
            st.download_button(
                "📄 修正後のA4をダウンロード",
                data=st.session_state.simple_admin_pdf,
                file_name=st.session_state.simple_admin_pdf_name,
                mime="application/pdf",
                use_container_width=True,
            )

        st.write("入庫待ちのA4再印刷・取消")
        pending_plans = list_receiving_plans(
            conn,
            status="入庫待ち",
        )

        if not pending_plans:
            st.info("入庫待ちはありません。")
        else:
            pending_map = {
                pending_plan_label(plan): plan
                for plan in pending_plans
            }
            selected_pending_label = st.selectbox(
                "入庫待ちを選択",
                options=list(pending_map.keys()),
                key="simple_admin_pending",
            )
            selected_pending = pending_map[selected_pending_label]
            selected_pending_code = str(
                row_value(selected_pending, "receipt_code", "")
            )
            pending_pdf = create_receiving_plan_a4_pdf(
                [selected_pending]
            )

            st.download_button(
                "選択したA4を再印刷",
                data=pending_pdf,
                file_name=f"receiving_{selected_pending_code}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

            with st.form("simple_admin_cancel_pending"):
                cancel_checked = st.checkbox(
                    f"{selected_pending_code} を取り消す"
                )
                cancel_submitted = st.form_submit_button(
                    "入庫待ちを取消",
                    use_container_width=True,
                )

            if cancel_submitted:
                if not cancel_checked:
                    st.warning("取消確認にチェックを入れてください。")
                else:
                    try:
                        cancel_receiving_plan(
                            conn,
                            selected_pending_code,
                        )
                        st.session_state.simple_admin_flash = (
                            f"{selected_pending_code} を取り消しました。"
                        )
                        st.rerun()
                    except PalletError as exc:
                        st.error(str(exc))

        st.divider()
        st.write("入庫済み・未出庫データの修正")
        editable_batches = list_editable_pallet_batches(conn)

        if not editable_batches:
            st.info("修正できる未出庫データはありません。")
        else:
            editable_map = {
                editable_batch_label(batch): batch
                for batch in editable_batches
            }
            selected_edit_label = st.selectbox(
                "修正する登録を選択",
                options=list(editable_map.keys()),
                key="simple_admin_edit_batch",
            )
            selected_batch = editable_map[selected_edit_label]
            selected_batch_code = str(
                row_value(selected_batch, "batch_code", "")
            )
            edit_pallets = get_batch_pallets(
                conn,
                selected_batch_code,
            )

            if not edit_pallets:
                st.warning("対象が更新されています。画面を更新してください。")
            else:
                edit_companies = conn.execute(
                    """
                    SELECT id, code, name
                    FROM companies
                    ORDER BY code, name
                    """
                ).fetchall()
                edit_company_map = company_option_map(edit_companies)
                edit_company_labels = list(edit_company_map.keys())
                current_company_id = int(
                    row_value(edit_pallets[0], "company_id", 0)
                )
                current_company_index = next(
                    (
                        index
                        for index, label in enumerate(edit_company_labels)
                        if edit_company_map[label]["id"]
                        == current_company_id
                    ),
                    0,
                )

                current_category_name = str(
                    row_value(
                        edit_pallets[0],
                        "category_name",
                        "未分類",
                    )
                    or "未分類"
                ).strip()
                category_is_unclassified = current_category_name in (
                    "",
                    "未分類",
                )
                editable_category_map = {
                    str(row_value(category, "name", "")): int(
                        row_value(category, "id", 0)
                    )
                    for category in list_pallet_categories(conn)
                    if str(row_value(category, "name", "")).strip()
                    not in ("", "未分類")
                }

                allocation_rows = [
                    {
                        "管理番号": (
                            f"{int(row_value(pallet, 'category_sequence', 0)):03d}"
                        ),
                        "個数": int(
                            row_value(pallet, "initial_qty", 0)
                        ),
                    }
                    for pallet in edit_pallets
                ]

                with st.form(
                    f"simple_admin_edit_form_{selected_batch_code}"
                ):
                    edit_company_label = st.selectbox(
                        "業者名",
                        options=edit_company_labels,
                        index=current_company_index,
                    )

                    if category_is_unclassified:
                        category_options = ["未分類のまま"] + list(
                            editable_category_map.keys()
                        )
                        edit_category_label = st.selectbox(
                            "大カテゴリー",
                            options=category_options,
                        )
                    else:
                        st.text_input(
                            "大カテゴリー",
                            value=current_category_name,
                            disabled=True,
                        )
                        edit_category_label = ""

                    item_col1, item_col2 = st.columns(2)

                    with item_col1:
                        edit_item_code = st.text_input(
                            "商品コード（任意）",
                            value=str(
                                row_value(
                                    edit_pallets[0],
                                    "item_code",
                                    "",
                                )
                                or ""
                            ),
                        )

                    with item_col2:
                        edit_item_name = st.text_input(
                            "商品名",
                            value=str(
                                row_value(
                                    edit_pallets[0],
                                    "item_name",
                                    "",
                                )
                                or ""
                            ),
                        )

                    edited_allocations = st.data_editor(
                        pd.DataFrame(allocation_rows),
                        hide_index=True,
                        use_container_width=True,
                        num_rows="fixed",
                        key=(
                            "simple_admin_allocations_"
                            f"{selected_batch_code}"
                        ),
                        column_config={
                            "管理番号": st.column_config.TextColumn(
                                "管理番号",
                                disabled=True,
                            ),
                            "個数": st.column_config.NumberColumn(
                                "商品数",
                                min_value=0,
                                max_value=1_000_000,
                                step=1,
                                required=True,
                                format="%d 個",
                            ),
                        },
                    )
                    update_submitted = st.form_submit_button(
                        "変更を保存",
                        type="primary",
                        use_container_width=True,
                    )

                if update_submitted:
                    try:
                        normalized_edit_name = str(
                            edit_item_name or ""
                        ).strip()

                        if not normalized_edit_name:
                            raise PalletError("商品名を入力してください。")

                        selected_category_id = None

                        if (
                            category_is_unclassified
                            and edit_category_label != "未分類のまま"
                        ):
                            selected_category_id = editable_category_map[
                                edit_category_label
                            ]

                        edited_allocations["個数"] = (
                            pd.to_numeric(
                                edited_allocations["個数"],
                                errors="coerce",
                            )
                            .fillna(0)
                            .astype(int)
                        )
                        result = update_pallet_batch(
                            conn=conn,
                            batch_code=selected_batch_code,
                            company_id=edit_company_map[
                                edit_company_label
                            ]["id"],
                            project_id=None,
                            item_id=None,
                            allocations=edited_allocations.to_dict(
                                "records"
                            ),
                            item_code=str(edit_item_code or "").strip(),
                            item_name=normalized_edit_name,
                            category_id=selected_category_id,
                        )
                        revised_pallets = get_batch_pallets(
                            conn,
                            selected_batch_code,
                        )
                        st.session_state.simple_admin_pdf = (
                            create_pallet_a4_pdf(revised_pallets)
                        )
                        st.session_state.simple_admin_pdf_name = (
                            f"pallet_{selected_batch_code}.pdf"
                        )
                        st.session_state.simple_admin_flash = (
                            "変更しました。 "
                            f"商品総数：{int(result['total_qty']):,}個"
                        )
                        st.rerun()

                    except PalletError as exc:
                        st.error(str(exc))

                    except Exception as exc:
                        st.error(
                            "変更中にエラーが発生しました："
                            f"{exc}"
                        )

                st.write("誤登録の削除")

                with st.form(
                    f"simple_admin_delete_form_{selected_batch_code}"
                ):
                    delete_checked = st.checkbox(
                        f"{selected_batch_code} を削除する"
                    )
                    delete_submitted = st.form_submit_button(
                        "登録を削除",
                        use_container_width=True,
                    )

                if delete_submitted:
                    if not delete_checked:
                        st.warning("削除確認にチェックを入れてください。")
                    else:
                        try:
                            delete_pallet_batch(
                                conn,
                                selected_batch_code,
                            )
                            st.session_state.simple_admin_pdf = None
                            st.session_state.simple_admin_pdf_name = ""
                            st.session_state.simple_admin_flash = (
                                "誤登録を削除しました。"
                            )
                            st.rerun()

                        except PalletError as exc:
                            st.error(str(exc))

                        except Exception as exc:
                            st.error(
                                "削除中にエラーが発生しました："
                                f"{exc}"
                            )


conn.close()
