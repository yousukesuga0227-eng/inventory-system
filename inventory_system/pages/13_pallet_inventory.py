from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import streamlit as st

from auth import check_login
from database import get_connection
from pages.pallet.pallet_db import (
    PalletError,
    cancel_receiving_plan,
    confirm_receiving_plan,
    create_pallet_batch,
    create_receiving_plan,
    create_receiving_plans,
    delete_pallet_batch,
    get_batch_pallets,
    get_items_for_company,
    get_pallet_by_code,
    get_receiving_plan_by_code,
    list_editable_pallet_batches,
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
        "既存のパレット環境は、SHARKを停止して"
        "INSTALL_PALLET_RECEIVING_PLANS.py を一度だけ実行し、"
        "再起動してください。"
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
    "receiving_plan_preview": None,
    "receiving_plan_last_pdf": None,
    "receiving_plan_last_pdf_name": "",
    "receiving_plan_last_code": "",
    "receiving_plan_flash": "",
    "receiving_plan_scan_target": None,
    "receiving_plan_confirm_flash": "",
    "receiving_plan_csv_pdf": None,
    "receiving_plan_csv_pdf_name": "",
    "receiving_plan_csv_flash": "",
    "receiving_plan_csv_uploader_version": 0,
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


def item_option_map(items):
    options = {}

    for item in items:
        item_id = int(row_value(item, "id", 0))
        item_code = str(row_value(item, "code", "") or "").strip()
        item_name = str(row_value(item, "name", "") or "").strip()
        label = f"{item_code} - {item_name}" if item_code else item_name

        if label in options:
            label = f"{label}（商品ID：{item_id}）"

        options[label] = {
            "id": item_id,
            "code": item_code,
            "name": item_name,
            "project_id": row_value(item, "project_id"),
        }

    return options


def receiving_plan_dataframe(rows):
    return pd.DataFrame(
        [
            {
                "入庫管理番号": row_value(row, "receipt_code", ""),
                "入庫日": format_date(
                    row_value(row, "receiving_date", "")
                ),
                "顧客": row_value(row, "company_name", ""),
                "商品名": row_value(row, "item_name", ""),
                "パレット枚数": int(
                    row_value(row, "pallet_count", 0)
                ),
                "1パレット商品数": int(
                    row_value(row, "qty_per_pallet", 0)
                ),
                "商品総数": int(row_value(row, "total_qty", 0)),
                "状態": row_value(row, "status", ""),
            }
            for row in rows
        ]
    )


def _normalized_master_value(value):
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value or "").strip().casefold()


def validate_receiving_csv(dataframe, companies):
    required_columns = [
        "入庫日",
        "顧客",
        "商品名",
        "パレット枚数",
        "1パレットあたりの商品個数",
    ]
    missing_columns = [
        column
        for column in required_columns
        if column not in dataframe.columns
    ]

    if missing_columns:
        return [], [
            "不足列：" + "、".join(missing_columns)
        ]

    company_rows = []

    for company in companies:
        company_rows.append(
            {
                "id": int(row_value(company, "id", 0)),
                "code": _normalized_master_value(
                    row_value(company, "code", "")
                ),
                "name": _normalized_master_value(
                    row_value(company, "name", "")
                ),
            }
        )

    valid_plans = []
    errors = []

    for source_index, row in dataframe.iterrows():
        row_number = int(source_index) + 2

        if all(pd.isna(row[column]) for column in required_columns):
            continue

        company_text = _normalized_master_value(row["顧客"])
        company_matches = [
            company
            for company in company_rows
            if company_text in {company["code"], company["name"]}
        ]

        if len(company_matches) != 1:
            errors.append(
                f"{row_number}行目：顧客「{row['顧客']}」を"
                "企業マスターから1件に特定できません。"
            )
            continue

        raw_item_name = row["商品名"]

        if pd.isna(raw_item_name):
            item_name = ""
        else:
            item_name = str(raw_item_name).strip()

        if not item_name:
            errors.append(
                f"{row_number}行目：商品名を入力してください。"
            )
            continue

        parsed_date = pd.to_datetime(row["入庫日"], errors="coerce")

        if pd.isna(parsed_date):
            errors.append(
                f"{row_number}行目：入庫日の形式が正しくありません。"
            )
            continue

        try:
            pallet_count_value = float(row["パレット枚数"])
            qty_value = float(row["1パレットあたりの商品個数"])

            if (
                not pallet_count_value.is_integer()
                or not qty_value.is_integer()
            ):
                raise ValueError

            pallet_count = int(pallet_count_value)
            qty_per_pallet = int(qty_value)
        except (TypeError, ValueError):
            errors.append(
                f"{row_number}行目：パレット枚数と商品個数は"
                "整数で入力してください。"
            )
            continue

        if not (1 <= pallet_count <= 1000):
            errors.append(
                f"{row_number}行目：パレット枚数は"
                "1～1,000枚で入力してください。"
            )
            continue

        if not (1 <= qty_per_pallet <= 1_000_000):
            errors.append(
                f"{row_number}行目：1パレットの商品個数は"
                "1～1,000,000個で入力してください。"
            )
            continue

        company = company_matches[0]
        valid_plans.append(
            {
                "receiving_date": parsed_date.date(),
                "company_id": company["id"],
                "project_id": None,
                "item_id": None,
                "item_name": item_name,
                "pallet_count": pallet_count,
                "qty_per_pallet": qty_per_pallet,
            }
        )

    if not valid_plans and not errors:
        errors.append("登録できるデータ行がありません。")

    return valid_plans, errors


# ============================================================
# タブ
# ============================================================
(
    tab_receiving_plan,
    tab_receiving_confirm,
    tab_receiving,
    tab_shipping,
    tab_stock,
    tab_history,
) = st.tabs(
    [
        "事前登録・A4",
        "QR入庫",
        "直接入庫",
        "QR出庫",
        "在庫確認",
        "入出庫履歴",
    ]
)


# ============================================================
# 事前登録・A4発行（パターン1）
# ============================================================
with tab_receiving_plan:
    st.subheader("入庫予定を事前登録してA4管理票を発行")
    st.info(
        "入庫前に入力してA4横向き管理票を印刷します。"
        "管理票は1パレットにつき1ページです。"
    )
    st.caption(
        "案件の選択はありません。商品名はマスター登録なしで"
        "そのまま入力できます。"
    )

    if st.session_state.receiving_plan_flash:
        st.success(st.session_state.receiving_plan_flash)
        st.session_state.receiving_plan_flash = ""

    if st.session_state.receiving_plan_last_pdf is not None:
        st.download_button(
            "📄 A4横向き管理票を開く・ダウンロード",
            data=st.session_state.receiving_plan_last_pdf,
            file_name=st.session_state.receiving_plan_last_pdf_name,
            mime="application/pdf",
            use_container_width=True,
        )
        st.caption(
            "PDFを開いて Ctrl＋P で印刷してください。"
            f" 入庫管理番号：{st.session_state.receiving_plan_last_code}"
        )

    plan_form_tab, plan_csv_tab, plan_list_tab = st.tabs(
        ["入力フォーム", "CSV一括登録", "入庫待ち一覧"]
    )

    master_companies = conn.execute(
        """
        SELECT id, code, name
        FROM companies
        ORDER BY code, name
        """
    ).fetchall()
    plan_company_map = company_option_map(master_companies)

    with plan_form_tab:
        if not plan_company_map:
            st.warning("企業マスターに顧客が登録されていません。")
        else:
            with st.form("receiving_plan_input_form"):
                receiving_date_value = st.date_input(
                    "入庫日",
                    value=date.today(),
                )
                company_label = st.selectbox(
                    "顧客",
                    options=list(plan_company_map.keys()),
                )
                item_name_value = st.text_input(
                    "商品名",
                    placeholder="商品名を入力",
                )
                count_col, quantity_col = st.columns(2)

                with count_col:
                    plan_pallet_count = st.number_input(
                        "パレット枚数",
                        min_value=1,
                        max_value=1000,
                        value=1,
                        step=1,
                    )

                with quantity_col:
                    plan_qty_per_pallet = st.number_input(
                        "1パレットあたりの商品個数",
                        min_value=1,
                        max_value=1_000_000,
                        value=1,
                        step=1,
                    )

                preview_submitted = st.form_submit_button(
                    "入力内容を確認",
                    type="primary",
                    use_container_width=True,
                )

            if preview_submitted:
                normalized_item_name = str(item_name_value or "").strip()

                if not normalized_item_name:
                    st.session_state.receiving_plan_preview = None
                    st.error("商品名を入力してください。")
                else:
                    selected_company = plan_company_map[company_label]
                    st.session_state.receiving_plan_preview = {
                        "receiving_date": receiving_date_value,
                        "company_id": selected_company["id"],
                        "company_name": selected_company["name"],
                        "project_id": None,
                        "item_id": None,
                        "item_name": normalized_item_name,
                        "item_code": "",
                        "pallet_count": int(plan_pallet_count),
                        "qty_per_pallet": int(plan_qty_per_pallet),
                        "total_qty": (
                            int(plan_pallet_count)
                            * int(plan_qty_per_pallet)
                        ),
                    }

            plan_preview = st.session_state.receiving_plan_preview

            if plan_preview is not None:
                st.divider()
                st.subheader("登録・印刷内容の確認")
                preview_col1, preview_col2, preview_col3 = st.columns(3)

                with preview_col1:
                    st.metric(
                        "入庫日",
                        format_date(plan_preview["receiving_date"]),
                    )
                    st.metric("顧客", plan_preview["company_name"])

                with preview_col2:
                    st.metric("商品名", plan_preview["item_name"])
                    st.metric(
                        "パレット枚数",
                        f"{plan_preview['pallet_count']:,} 枚",
                    )

                with preview_col3:
                    st.metric(
                        "1パレットの商品数",
                        f"{plan_preview['qty_per_pallet']:,} 個",
                    )
                    st.metric(
                        "商品総数",
                        f"{plan_preview['total_qty']:,} 個",
                    )

                st.warning(
                    "この内容で事前登録し、A4をプリントアウトしますか？"
                )
                ok_col, back_col = st.columns(2)

                with ok_col:
                    create_plan_submitted = st.button(
                        "OK：登録してA4 PDFを作成",
                        type="primary",
                        use_container_width=True,
                    )

                with back_col:
                    return_to_input = st.button(
                        "入力を修正する",
                        use_container_width=True,
                    )

                if return_to_input:
                    st.session_state.receiving_plan_preview = None
                    st.rerun()

                if create_plan_submitted:
                    try:
                        receipt_code = create_receiving_plan(
                            conn=conn,
                            receiving_date=plan_preview[
                                "receiving_date"
                            ],
                            company_id=plan_preview["company_id"],
                            project_id=plan_preview["project_id"],
                            item_id=plan_preview["item_id"],
                            pallet_count=plan_preview["pallet_count"],
                            qty_per_pallet=plan_preview[
                                "qty_per_pallet"
                            ],
                            username=username,
                            item_name=plan_preview["item_name"],
                        )
                        registered_plan = get_receiving_plan_by_code(
                            conn,
                            receipt_code,
                        )
                        pdf_data = create_receiving_plan_a4_pdf(
                            [registered_plan]
                        )
                        st.session_state.receiving_plan_last_pdf = pdf_data
                        st.session_state.receiving_plan_last_pdf_name = (
                            f"receiving_{receipt_code}.pdf"
                        )
                        st.session_state.receiving_plan_last_code = (
                            receipt_code
                        )
                        st.session_state.receiving_plan_flash = (
                            "事前登録が完了しました！"
                            f" A4は{plan_preview['pallet_count']:,}ページです。"
                        )
                        st.session_state.receiving_plan_preview = None
                        st.rerun()

                    except PalletError as exc:
                        st.error(str(exc))

                    except Exception as exc:
                        st.error(
                            "入庫予定の登録中にエラーが"
                            f"発生しました：{exc}"
                        )

    with plan_csv_tab:
        st.write("複数の入庫予定をCSVからまとめて登録できます。")
        csv_template = pd.DataFrame(
            [
                {
                    "入庫日": date.today().isoformat(),
                    "顧客": "顧客名",
                    "商品名": "商品名",
                    "パレット枚数": 1,
                    "1パレットあたりの商品個数": 1,
                }
            ]
        )
        st.download_button(
            "CSVひな形をダウンロード",
            data=csv_template.to_csv(
                index=False
            ).encode("utf-8-sig"),
            file_name="pallet_receiving_template.csv",
            mime="text/csv",
        )
        st.caption(
            "顧客は企業マスターと同じ表記にしてください。"
            "商品名はCSVに入力した文字をそのまま登録します。"
        )

        if st.session_state.receiving_plan_csv_flash:
            st.success(st.session_state.receiving_plan_csv_flash)
            st.session_state.receiving_plan_csv_flash = ""

        if st.session_state.receiving_plan_csv_pdf is not None:
            st.download_button(
                "📄 CSV登録分のA4管理票をダウンロード",
                data=st.session_state.receiving_plan_csv_pdf,
                file_name=st.session_state.receiving_plan_csv_pdf_name,
                mime="application/pdf",
                use_container_width=True,
            )

        uploaded_csv = st.file_uploader(
            "入庫予定CSV",
            type=["csv"],
            key=(
                "receiving_plan_csv_file_"
                f"{st.session_state.receiving_plan_csv_uploader_version}"
            ),
        )

        if uploaded_csv is not None:
            try:
                csv_bytes = uploaded_csv.getvalue()

                try:
                    csv_dataframe = pd.read_csv(
                        BytesIO(csv_bytes),
                        encoding="utf-8-sig",
                        dtype=object,
                    )
                except UnicodeDecodeError:
                    csv_dataframe = pd.read_csv(
                        BytesIO(csv_bytes),
                        encoding="cp932",
                        dtype=object,
                    )

                csv_plans, csv_errors = validate_receiving_csv(
                    csv_dataframe,
                    master_companies,
                )

                if csv_errors:
                    st.error("CSVに修正が必要です。")

                    for error_message in csv_errors:
                        st.write(f"- {error_message}")

                else:
                    csv_required_columns = [
                        "入庫日",
                        "顧客",
                        "商品名",
                        "パレット枚数",
                        "1パレットあたりの商品個数",
                    ]
                    csv_preview = csv_dataframe.loc[
                        ~csv_dataframe[csv_required_columns]
                        .isna()
                        .all(axis=1)
                    ].copy()
                    csv_preview["商品総数"] = [
                        int(plan["pallet_count"])
                        * int(plan["qty_per_pallet"])
                        for plan in csv_plans
                    ]
                    st.dataframe(
                        csv_preview,
                        hide_index=True,
                        use_container_width=True,
                    )
                    csv_total_pages = sum(
                        int(plan["pallet_count"])
                        for plan in csv_plans
                    )
                    st.info(
                        f"入庫予定 {len(csv_plans):,}件・"
                        f"A4 {csv_total_pages:,}ページを作成します。"
                    )

                    if st.button(
                        "CSVを登録してA4 PDFを作成",
                        type="primary",
                        use_container_width=True,
                    ):
                        receipt_codes = create_receiving_plans(
                            conn=conn,
                            plans=csv_plans,
                            username=username,
                        )
                        registered_plans = [
                            get_receiving_plan_by_code(conn, code)
                            for code in receipt_codes
                        ]
                        csv_pdf = create_receiving_plan_a4_pdf(
                            registered_plans
                        )
                        st.session_state.receiving_plan_csv_pdf = (
                            csv_pdf
                        )
                        st.session_state.receiving_plan_csv_pdf_name = (
                            "pallet_receiving_csv_"
                            f"{datetime.now(JST):%Y%m%d_%H%M%S}.pdf"
                        )
                        st.session_state.receiving_plan_csv_flash = (
                            f"{len(receipt_codes):,}件を事前登録しました！"
                        )
                        st.session_state[
                            "receiving_plan_csv_uploader_version"
                        ] += 1
                        st.rerun()

            except PalletError as exc:
                st.error(str(exc))

            except Exception as exc:
                st.error(f"CSVの処理中にエラーが発生しました：{exc}")

    with plan_list_tab:
        pending_plans = list_receiving_plans(
            conn,
            status="入庫待ち",
        )

        if not pending_plans:
            st.info("現在、入庫待ちの管理票はありません。")
        else:
            st.dataframe(
                receiving_plan_dataframe(pending_plans),
                hide_index=True,
                use_container_width=True,
            )
            pending_map = {
                (
                    f"{row_value(plan, 'receipt_code', '')}｜"
                    f"{row_value(plan, 'company_name', '')}｜"
                    f"{row_value(plan, 'item_name', '')}"
                ): plan
                for plan in pending_plans
            }
            pending_label = st.selectbox(
                "管理票の再発行・取消",
                options=list(pending_map.keys()),
            )
            pending_plan = pending_map[pending_label]
            pending_pdf = create_receiving_plan_a4_pdf([pending_plan])
            st.download_button(
                "選択したA4管理票を再発行",
                data=pending_pdf,
                file_name=(
                    "receiving_"
                    f"{row_value(pending_plan, 'receipt_code', '')}.pdf"
                ),
                mime="application/pdf",
                use_container_width=True,
            )

            with st.expander("この入庫予定を取り消す"):
                with st.form("cancel_receiving_plan_form"):
                    receipt_code_to_cancel = row_value(
                        pending_plan,
                        "receipt_code",
                        "",
                    )
                    cancel_checked = st.checkbox(
                        f"{receipt_code_to_cancel} を取り消す"
                    )
                    cancel_submitted = st.form_submit_button(
                        "入庫予定を取消",
                        use_container_width=True,
                    )

                if cancel_submitted:
                    if not cancel_checked:
                        st.warning("取消確認にチェックを入れてください。")
                    else:
                        try:
                            cancel_receiving_plan(
                                conn,
                                receipt_code_to_cancel,
                            )
                            st.session_state.receiving_plan_flash = (
                                f"{receipt_code_to_cancel} を取り消しました。"
                            )
                            st.rerun()
                        except PalletError as exc:
                            st.error(str(exc))


# ============================================================
# QR入庫確定（パターン1）
# ============================================================
with tab_receiving_confirm:
    st.subheader("A4管理票のQRを読み取って入庫確定")
    st.info(
        "印刷済みA4管理票のQRを読み取ります。"
        "どのパレットのQRでも入庫予定全体を呼び出せます。"
    )

    if st.session_state.receiving_plan_confirm_flash:
        st.success(st.session_state.receiving_plan_confirm_flash)
        st.session_state.receiving_plan_confirm_flash = ""

    with st.form("receiving_plan_qr_form", clear_on_submit=True):
        receiving_qr_code = st.text_input(
            "入庫管理票QR",
            placeholder="IN-260731-001-P001",
        )
        receiving_qr_submitted = st.form_submit_button(
            "QRを確認",
            type="primary",
            use_container_width=True,
        )

    if receiving_qr_submitted:
        normalized_receiving_qr = str(
            receiving_qr_code or ""
        ).strip().upper()

        if not normalized_receiving_qr:
            st.warning("QRコードを読み取ってください。")
        else:
            st.session_state.receiving_plan_scan_target = (
                normalized_receiving_qr
            )

    receiving_scan_target = (
        st.session_state.receiving_plan_scan_target
    )

    if receiving_scan_target:
        receiving_plan = get_receiving_plan_by_code(
            conn,
            receiving_scan_target,
        )

        if receiving_plan is None:
            st.error("入庫管理票が見つかりません。")
            st.session_state.receiving_plan_scan_target = None
        else:
            st.divider()
            st.write(
                f"**入庫管理番号：** "
                f"{row_value(receiving_plan, 'receipt_code', '')}"
            )
            detail_col1, detail_col2, detail_col3 = st.columns(3)

            with detail_col1:
                st.metric(
                    "入庫日",
                    format_date(
                        row_value(receiving_plan, "receiving_date", "")
                    ),
                )
                st.metric(
                    "顧客",
                    row_value(receiving_plan, "company_name", ""),
                )

            with detail_col2:
                st.metric(
                    "商品名",
                    row_value(receiving_plan, "item_name", ""),
                )
                st.metric(
                    "パレット枚数",
                    f"{int(row_value(receiving_plan, 'pallet_count', 0)):,} 枚",
                )

            with detail_col3:
                st.metric(
                    "1パレットの商品数",
                    f"{int(row_value(receiving_plan, 'qty_per_pallet', 0)):,} 個",
                )
                st.metric(
                    "商品総数",
                    f"{int(row_value(receiving_plan, 'total_qty', 0)):,} 個",
                )

            plan_status = row_value(receiving_plan, "status", "")

            if plan_status == "入庫待ち":
                st.warning(
                    "内容と現物を確認してから入庫確定してください。"
                    "確定すると商品総数で入庫履歴に登録されます。"
                )

                if st.button(
                    "✅ この内容で入庫確定",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        result = confirm_receiving_plan(
                            conn=conn,
                            receipt_or_pallet_code=(
                                receiving_scan_target
                            ),
                            username=username,
                        )
                        st.session_state.receiving_plan_confirm_flash = (
                            f"{result['receipt_code']} を入庫登録しました！ "
                            f"商品総数：{int(result['total_qty']):,}個"
                        )
                        st.session_state.receiving_plan_scan_target = None
                        st.rerun()

                    except PalletError as exc:
                        st.error(str(exc))

                    except Exception as exc:
                        st.error(
                            "入庫確定中にエラーが"
                            f"発生しました：{exc}"
                        )

            elif plan_status == "入庫済み":
                st.info(
                    "この管理票は入庫登録済みです。"
                    "印刷済みQRはそのままQR出庫に使えます。"
                )
            else:
                st.error("この管理票は取り消されています。")

            if st.button(
                "読み取りをやり直す",
                use_container_width=True,
            ):
                st.session_state.receiving_plan_scan_target = None
                st.rerun()


# ============================================================
# 直接入庫登録（パターン2）
# ============================================================
with tab_receiving:
    st.subheader("直接入庫登録")

    st.info(
        "A4の事前発行を使わず、その場で入庫登録する方式です。"
        "パレットごとの数量を自動または手動で割り振れます。"
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
            current_item_name = str(
                row_value(edit_pallets[0], "item_name", "") or ""
            ).strip()
            free_name_batch = current_item_id in ("", None)

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
                if free_name_batch:
                    edit_item_name_value = st.text_input(
                        "商品名",
                        value=current_item_name,
                    )
                else:
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
                    if free_name_batch:
                        selected_edit_project_id = None
                        selected_edit_item_id = None
                        selected_edit_item_name = str(
                            edit_item_name_value or ""
                        ).strip()
                    else:
                        selected_edit_item = edit_item_map[
                            edit_item_label
                        ]
                        selected_edit_project_id = row_value(
                            selected_edit_item,
                            "project_id",
                        )
                        selected_edit_item_id = row_value(
                            selected_edit_item,
                            "id",
                        )
                        selected_edit_item_name = row_value(
                            selected_edit_item,
                            "name",
                            "",
                        )

                    result = update_pallet_batch(
                        conn=conn,
                        batch_code=selected_edit_batch_code,
                        company_id=row_value(
                            selected_edit_company,
                            "id",
                        ),
                        project_id=selected_edit_project_id,
                        item_id=selected_edit_item_id,
                        allocations=edited_allocations.to_dict(
                            "records"
                        ),
                        item_name=selected_edit_item_name,
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
