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
    get_pallet_category_numbering_limit,
    get_batch_pallets,
    get_items_for_company,
    get_pallet_by_code,
    get_receiving_plan_by_code,
    list_editable_pallet_batches,
    list_pallet_categories,
    list_pallet_history,
    list_pallet_numbering_registrations,
    list_pallet_stock,
    list_receiving_plans,
    renumber_pallet_registration,
    set_pallet_category_next_sequence,
    ship_pallet,
    ship_pallet_batch,
    update_pallet_batch,
)
from pages.pallet.pallet_documents import (
    create_pallet_a4_pdf,
    create_receiving_plan_a4_pdf,
)
from pages.pallet.pallet_shipping_documents import (
    create_pallet_shipment_document,
)
from pages.pallet.pallet_tables import (
    PalletSchemaError,
    validate_pallet_database,
)


from pages.pallet.pallet_item_numbering import (
    PalletItemNumberingError,
    apply_existing_pallet_item_numbering,
    list_pallet_category_code_settings,
    preview_existing_pallet_item_numbering,
    set_pallet_category_code,
)

# === SHARK PALLET ITEM NUMBERING UI START ===

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
    "simple_pallet_out_rows": [],
    "simple_pallet_out_documents": None,
    "simple_pallet_out_company_id": None,
    "simple_pallet_out_company_name": "",
    "simple_pallet_out_form_version": 0,
    "simple_admin_flash": "",
    "simple_admin_pdf": None,
    "simple_admin_pdf_name": "",
    "simple_admin_action": "",
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



# === SHARK PRODUCT CODE STOCK SUMMARY HELPER START ===
def stock_row_matches(row, search_text):
    # 現在庫の1パレットが検索語に一致するか判定する。
    normalized_search = str(search_text or "").strip().casefold()

    if not normalized_search:
        return True

    management_sequence = int(
        row_value(row, "category_sequence", 0)
    )
    searchable_values = [
        row_value(row, "company_code", ""),
        row_value(row, "company_name", ""),
        row_value(row, "category_name", ""),
        row_value(row, "item_code", ""),
        row_value(row, "item_name", ""),
        row_value(row, "pallet_code", ""),
        row_value(row, "batch_code", ""),
        str(management_sequence),
        f"{management_sequence:03d}",
    ]
    searchable_text = " ".join(
        str(value or "")
        for value in searchable_values
    ).casefold()
    return normalized_search in searchable_text


def product_code_stock_dataframe(rows):
    # 保管中パレットを商品コード単位でまとめる。
    # 未採番の旧データだけは顧客・カテゴリー・商品名で分ける。
    grouped = {}

    for row in rows:
        company_code = str(
            row_value(row, "company_code", "") or ""
        ).strip()
        company_name = str(
            row_value(row, "company_name", "") or ""
        ).strip()
        category_name = str(
            row_value(row, "category_name", "") or ""
        ).strip()
        item_code = str(
            row_value(row, "item_code", "") or ""
        ).strip().upper()
        item_name = str(
            row_value(row, "item_name", "") or ""
        ).strip()
        current_qty = int(
            row_value(row, "current_qty", 0)
        )

        if item_code:
            group_key = (
                "CODE",
                company_code.casefold(),
                category_name.casefold(),
                item_code.casefold(),
            )
        else:
            group_key = (
                "NO_CODE",
                company_code.casefold(),
                company_name.casefold(),
                category_name.casefold(),
                item_name.casefold(),
            )

        if group_key not in grouped:
            grouped[group_key] = {
                "顧客コード": company_code,
                "顧客名": company_name,
                "大カテゴリー": category_name,
                "商品コード": item_code or "未採番",
                "商品名": item_name,
                "保管パレット数": 0,
                "現在庫合計": 0,
            }

        grouped[group_key]["保管パレット数"] += 1
        grouped[group_key]["現在庫合計"] += current_qty

    summary_rows = sorted(
        grouped.values(),
        key=lambda row: (
            str(row["顧客コード"]),
            str(row["顧客名"]),
            str(row["大カテゴリー"]),
            str(row["商品コード"]),
            str(row["商品名"]),
        ),
    )

    return pd.DataFrame(
        summary_rows,
        columns=[
            "顧客コード",
            "顧客名",
            "大カテゴリー",
            "商品コード",
            "商品名",
            "保管パレット数",
            "現在庫合計",
        ],
    )
# === SHARK PRODUCT CODE STOCK SUMMARY HELPER END ===


# === SHARK PALLET STOCK DASHBOARD HELPERS START ===
def compact_pallet_dataframe(rows):
    # 現在保管中のパレットだけを簡潔な表にする。
    compact_rows = []

    for row in rows:
        compact_rows.append(
            {
                "管理番号": (
                    f"{int(row_value(row, 'category_sequence', 0)):03d}"
                ),
                "商品コード": row_value(row, "item_code", "") or "未採番",
                "商品名": row_value(row, "item_name", ""),
                "個数": int(row_value(row, "current_qty", 0)),
            }
        )

    compact_rows.sort(
        key=lambda row: (
            str(row["商品コード"]),
            str(row["商品名"]),
            str(row["管理番号"]),
        )
    )

    return pd.DataFrame(
        compact_rows,
        columns=[
            "管理番号",
            "商品コード",
            "商品名",
            "個数",
        ],
    )


def company_category_stock_dataframe(rows):
    # 現在庫を荷主×大カテゴリー単位にまとめる。
    grouped = {}

    for row in rows:
        company_name = str(
            row_value(row, "company_name", "") or "荷主未設定"
        ).strip()
        category_name = str(
            row_value(row, "category_name", "") or "未分類"
        ).strip()
        current_qty = int(row_value(row, "current_qty", 0))

        key = (
            company_name.casefold(),
            category_name.casefold(),
        )

        if key not in grouped:
            grouped[key] = {
                "荷主": company_name,
                "大カテゴリー": category_name,
                "現在パレ": 0,
                "現在個数": 0,
            }

        grouped[key]["現在パレ"] += 1
        grouped[key]["現在個数"] += current_qty

    summary = sorted(
        grouped.values(),
        key=lambda row: (
            str(row["荷主"]),
            str(row["大カテゴリー"]),
        ),
    )

    return pd.DataFrame(
        summary,
        columns=[
            "荷主",
            "大カテゴリー",
            "現在パレ",
            "現在個数",
        ],
    )


def parse_history_datetime(value):
    # 履歴日時をJST aware datetimeへ変換する。
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()

        if not text:
            return None

        try:
            parsed = datetime.fromisoformat(
                text.replace("Z", "+00:00")
            )
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=JST)

    return parsed.astimezone(JST)


def daily_pallet_summary_dataframe(rows, days=14, now=None):
    # 前日19:00超～当日19:00を1締め日として集計する。
    now = now or datetime.now(JST)

    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)

    events = []
    all_keys = set()

    for row in rows:
        event_at = parse_history_datetime(
            row_value(row, "created_at", "")
        )

        if event_at is None:
            continue

        company_name = str(
            row_value(row, "company_name", "") or "荷主未設定"
        ).strip()
        category_name = str(
            row_value(row, "category_name", "") or "未分類"
        ).strip()
        history_type = str(
            row_value(row, "history_type", "") or ""
        ).strip()
        after_qty = int(
            row_value(row, "after_qty", 0) or 0
        )

        key = (company_name, category_name)
        all_keys.add(key)

        events.append(
            {
                "at": event_at,
                "key": key,
                "type": history_type,
                "after_qty": after_qty,
            }
        )

    today = now.date()
    days = max(1, int(days))
    output = []

    for offset in range(days):
        closing_day = today - timedelta(days=offset)
        scheduled_cutoff = datetime(
            closing_day.year,
            closing_day.month,
            closing_day.day,
            19,
            0,
            tzinfo=JST,
        )
        previous_cutoff = scheduled_cutoff - timedelta(days=1)

        if closing_day == today and now < scheduled_cutoff:
            effective_cutoff = now
            status_text = "LIVE"
        else:
            effective_cutoff = scheduled_cutoff
            status_text = "19:00確定"

        for company_name, category_name in sorted(all_keys):
            key = (company_name, category_name)

            received_pallets = 0
            shipped_pallets = 0
            closing_pallets = 0

            for event in events:
                if event["key"] != key:
                    continue

                event_at = event["at"]
                event_type = event["type"]
                is_full_ship = (
                    event_type == "出庫"
                    and event["after_qty"] <= 0
                )

                if event_at <= effective_cutoff:
                    if event_type == "入庫":
                        closing_pallets += 1
                    elif is_full_ship:
                        closing_pallets -= 1

                if previous_cutoff < event_at <= effective_cutoff:
                    if event_type == "入庫":
                        received_pallets += 1
                    elif is_full_ship:
                        shipped_pallets += 1

            closing_pallets = max(0, closing_pallets)

            if (
                closing_pallets == 0
                and received_pallets == 0
                and shipped_pallets == 0
            ):
                continue

            output.append(
                {
                    "締め日": closing_day.strftime("%Y-%m-%d"),
                    "状態": status_text,
                    "荷主": company_name,
                    "大カテゴリー": category_name,
                    "入庫パレ": received_pallets,
                    "出庫パレ": shipped_pallets,
                    "時点在庫パレ": closing_pallets,
                }
            )

    return pd.DataFrame(
        output,
        columns=[
            "締め日",
            "状態",
            "荷主",
            "大カテゴリー",
            "入庫パレ",
            "出庫パレ",
            "時点在庫パレ",
        ],
    )
# === SHARK PALLET STOCK DASHBOARD HELPERS END ===


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


def numbering_registration_label(registration):
    """採番管理の選択肢を、現在番号と商品が分かる表示にする。"""

    receipt_code = str(
        row_value(registration, "receipt_code", "") or ""
    ).strip()
    batch_code = str(
        row_value(registration, "batch_code", "") or ""
    ).strip()
    reference_code = receipt_code or batch_code
    return (
        f"{row_value(registration, 'registration_status', '')}｜"
        f"{reference_code}｜"
        f"{row_value(registration, 'category_name', '')} "
        f"{management_number(registration)}｜"
        f"{row_value(registration, 'company_name', '')}｜"
        f"{row_value(registration, 'item_name', '')}"
    )



# === SHARK UNCLASSIFIED CLEANUP HELPERS START ===
def list_unclassified_cleanup_candidates(conn):
    candidates = []

    plan_rows = conn.execute(
        """
        SELECT
            rp.id,
            rp.receipt_code,
            rp.status,
            rp.batch_code,
            c.name AS company_name,
            COALESCE(NULLIF(rp.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(rp.item_name, ''), i.name, '') AS item_name,
            rp.pallet_count,
            rp.created_at,
            (
                SELECT COUNT(*)
                FROM pallet_inventory pi
                WHERE
                    COALESCE(pi.is_deleted, FALSE) = FALSE
                    AND COALESCE(pi.batch_code, '') <> ''
                    AND pi.batch_code = rp.batch_code
            ) AS linked_inventory_count,
            (
                SELECT COALESCE(SUM(pi.current_qty), 0)
                FROM pallet_inventory pi
                WHERE
                    COALESCE(pi.is_deleted, FALSE) = FALSE
                    AND COALESCE(pi.batch_code, '') <> ''
                    AND pi.batch_code = rp.batch_code
            ) AS linked_current_qty_total
        FROM pallet_receiving_plans rp
        LEFT JOIN companies c ON c.id = rp.company_id
        LEFT JOIN items i ON i.id = rp.item_id
        LEFT JOIN pallet_categories pc ON pc.id = rp.category_id
        WHERE
            COALESCE(rp.is_deleted, FALSE) = FALSE
            AND rp.status = '入庫待ち'
            AND (
                COALESCE(NULLIF(TRIM(rp.category_name), ''), pc.name, '未分類')
                    = '未分類'
                OR pc.name = '未分類'
            )
        ORDER BY rp.created_at DESC, rp.id DESC
        """
    ).fetchall()

    for row in plan_rows:
        linked_count = int(
            row_value(row, "linked_inventory_count", 0) or 0
        )
        linked_current_qty = int(
            row_value(row, "linked_current_qty_total", 0) or 0
        )
        candidates.append(
            {
                "source_type": "plan",
                "source_id": int(row_value(row, "id", 0)),
                "batch_code": str(row_value(row, "batch_code", "") or ""),
                "区分": "入庫予定",
                "管理番号": str(row_value(row, "receipt_code", "") or ""),
                "状態": str(row_value(row, "status", "") or ""),
                "荷主": str(row_value(row, "company_name", "") or ""),
                "商品コード": str(row_value(row, "item_code", "") or ""),
                "商品名": str(row_value(row, "item_name", "") or ""),
                "パレット数": int(row_value(row, "pallet_count", 0) or 0),
                "現在個数": linked_current_qty,
                "登録日時": format_datetime(row_value(row, "created_at", "")),
                "can_cancel": linked_current_qty <= 0,
                "理由": (
                    ""
                    if linked_current_qty <= 0
                    else "現在庫が残っているため取消不可"
                ),
            }
        )

    inventory_rows = conn.execute(
        """
        SELECT
            MIN(pi.id) AS representative_id,
            COALESCE(pi.batch_code, '') AS batch_code,
            c.name AS company_name,
            COALESCE(NULLIF(pi.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(pi.item_name, ''), i.name, '') AS item_name,
            COUNT(*) AS pallet_count,
            COALESCE(SUM(pi.current_qty), 0) AS current_qty_total,
            MIN(pi.created_at) AS created_at
        FROM pallet_inventory pi
        LEFT JOIN companies c ON c.id = pi.company_id
        LEFT JOIN items i ON i.id = pi.item_id
        LEFT JOIN pallet_categories pc ON pc.id = pi.category_id
        WHERE
            COALESCE(pi.is_deleted, FALSE) = FALSE
            AND (
                COALESCE(NULLIF(TRIM(pi.category_name), ''), pc.name, '未分類')
                    = '未分類'
                OR pc.name = '未分類'
            )
        GROUP BY
            COALESCE(pi.batch_code, ''),
            c.name,
            COALESCE(NULLIF(pi.item_code, ''), i.code, ''),
            COALESCE(NULLIF(pi.item_name, ''), i.name, '')
        ORDER BY MIN(pi.created_at) DESC, MIN(pi.id) DESC
        """
    ).fetchall()

    for row in inventory_rows:
        current_qty = int(row_value(row, "current_qty_total", 0) or 0)
        batch_code = str(row_value(row, "batch_code", "") or "").strip()
        representative_id = int(
            row_value(row, "representative_id", 0) or 0
        )
        candidates.append(
            {
                "source_type": "inventory",
                "source_id": representative_id,
                "batch_code": batch_code,
                "区分": "過去在庫",
                "管理番号": batch_code or f"INV-{representative_id}",
                "状態": (
                    "在庫あり"
                    if current_qty > 0
                    else "在庫0・整理可能"
                ),
                "荷主": str(row_value(row, "company_name", "") or ""),
                "商品コード": str(row_value(row, "item_code", "") or ""),
                "商品名": str(row_value(row, "item_name", "") or ""),
                "パレット数": int(row_value(row, "pallet_count", 0) or 0),
                "現在個数": current_qty,
                "登録日時": format_datetime(row_value(row, "created_at", "")),
                "can_cancel": current_qty <= 0,
                "理由": (
                    ""
                    if current_qty <= 0
                    else "現在庫が残っているため取消不可"
                ),
            }
        )

    candidates.sort(
        key=lambda row: (
            str(row["登録日時"]),
            str(row["荷主"]),
            str(row["商品名"]),
        ),
        reverse=True,
    )
    return candidates


def cleanup_unclassified_candidate(conn, candidate):
    source_type = str(candidate.get("source_type") or "")
    source_id = int(candidate.get("source_id") or 0)
    batch_code = str(candidate.get("batch_code") or "").strip()
    now = datetime.now(JST)

    if source_type == "plan":
        plan = conn.execute(
            """
            SELECT id, batch_code
            FROM pallet_receiving_plans
            WHERE
                id = ?
                AND COALESCE(is_deleted, FALSE) = FALSE
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()

        if plan is None:
            raise PalletError("対象の入庫予定が見つかりません。")

        current_batch_code = str(
            row_value(plan, "batch_code", "") or ""
        ).strip()

        linked_count = 0
        linked_current_qty = 0

        if current_batch_code:
            linked_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS count_value,
                    COALESCE(SUM(current_qty), 0) AS current_qty_total
                FROM pallet_inventory
                WHERE
                    batch_code = ?
                    AND COALESCE(is_deleted, FALSE) = FALSE
                """,
                (current_batch_code,),
            ).fetchone()
            linked_count = int(
                row_value(linked_row, "count_value", 0) or 0
            )
            linked_current_qty = int(
                row_value(linked_row, "current_qty_total", 0) or 0
            )

        if linked_current_qty > 0:
            raise PalletError(
                f"現在庫が {linked_current_qty:,} 個残っているため取消できません。"
            )

        conn.execute(
            """
            UPDATE pallet_receiving_plans
            SET status = '取消', updated_at = ?
            WHERE
                id = ?
                AND COALESCE(is_deleted, FALSE) = FALSE
            """,
            (now, source_id),
        )

        if current_batch_code and linked_count > 0:
            conn.execute(
                """
                UPDATE pallet_inventory
                SET is_deleted = TRUE, updated_at = ?
                WHERE
                    batch_code = ?
                    AND COALESCE(is_deleted, FALSE) = FALSE
                    AND COALESCE(current_qty, 0) <= 0
                """,
                (now, current_batch_code),
            )

        conn.commit()
        return (
            "未分類データを整理しました。"
            "現在庫0のパレット本体は整理対象から除外し、入出庫履歴は残しています。"
        )

    if source_type == "inventory":
        if batch_code:
            stock_row = conn.execute(
                """
                SELECT COALESCE(SUM(current_qty), 0) AS current_qty_total
                FROM pallet_inventory
                WHERE
                    batch_code = ?
                    AND COALESCE(is_deleted, FALSE) = FALSE
                """,
                (batch_code,),
            ).fetchone()
        else:
            stock_row = conn.execute(
                """
                SELECT COALESCE(SUM(current_qty), 0) AS current_qty_total
                FROM pallet_inventory
                WHERE
                    id = ?
                    AND COALESCE(is_deleted, FALSE) = FALSE
                """,
                (source_id,),
            ).fetchone()

        current_qty = int(
            row_value(stock_row, "current_qty_total", 0) or 0
        )
        if current_qty > 0:
            raise PalletError(
                f"現在庫が {current_qty:,} 個残っているため取消できません。"
            )

        if batch_code:
            conn.execute(
                """
                UPDATE pallet_inventory
                SET is_deleted = TRUE, updated_at = ?
                WHERE
                    batch_code = ?
                    AND COALESCE(is_deleted, FALSE) = FALSE
                """,
                (now, batch_code),
            )
        else:
            conn.execute(
                """
                UPDATE pallet_inventory
                SET is_deleted = TRUE, updated_at = ?
                WHERE
                    id = ?
                    AND COALESCE(is_deleted, FALSE) = FALSE
                """,
                (now, source_id),
            )

        conn.commit()
        return (
            "在庫0の未分類データを採番対象から除外しました。"
            "入出庫履歴は残しています。"
        )

    raise PalletError("未分類データの区分が不正です。")
# === SHARK UNCLASSIFIED ZERO STOCK CLEANUP FIX ===

def force_cleanup_unclassified_candidate(conn, candidate):
    source_type = str(candidate.get("source_type") or "")
    source_id = int(candidate.get("source_id") or 0)
    batch_code = str(candidate.get("batch_code") or "").strip()
    now = datetime.now(JST)

    def inventory_rows_for_batch(target_batch_code, representative_id):
        if target_batch_code:
            return conn.execute(
                """
                SELECT
                    pi.id,
                    pi.current_qty,
                    COALESCE(
                        NULLIF(TRIM(pi.category_name), ''),
                        pc.name,
                        '未分類'
                    ) AS category_name
                FROM pallet_inventory pi
                LEFT JOIN pallet_categories pc ON pc.id = pi.category_id
                WHERE
                    pi.batch_code = ?
                    AND COALESCE(pi.is_deleted, FALSE) = FALSE
                ORDER BY pi.id
                """,
                (target_batch_code,),
            ).fetchall()

        return conn.execute(
            """
            SELECT
                pi.id,
                pi.current_qty,
                COALESCE(
                    NULLIF(TRIM(pi.category_name), ''),
                    pc.name,
                    '未分類'
                ) AS category_name
            FROM pallet_inventory pi
            LEFT JOIN pallet_categories pc ON pc.id = pi.category_id
            WHERE
                pi.id = ?
                AND COALESCE(pi.is_deleted, FALSE) = FALSE
            """,
            (representative_id,),
        ).fetchall()

    try:
        if source_type == "plan":
            plan = conn.execute(
                """
                SELECT
                    rp.id,
                    rp.batch_code,
                    COALESCE(
                        NULLIF(TRIM(rp.category_name), ''),
                        pc.name,
                        '未分類'
                    ) AS category_name
                FROM pallet_receiving_plans rp
                LEFT JOIN pallet_categories pc ON pc.id = rp.category_id
                WHERE
                    rp.id = ?
                    AND COALESCE(rp.is_deleted, FALSE) = FALSE
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()

            if plan is None:
                raise PalletError("対象の入庫予定が見つかりません。")

            category_name = str(
                row_value(plan, "category_name", "") or ""
            ).strip()

            if category_name != "未分類":
                raise PalletError(
                    "この入庫予定は未分類ではありません。強制整理を中止しました。"
                )

            current_batch_code = str(
                row_value(plan, "batch_code", "") or ""
            ).strip()

            linked_rows = (
                inventory_rows_for_batch(current_batch_code, 0)
                if current_batch_code
                else []
            )

            for linked_row in linked_rows:
                linked_category = str(
                    row_value(linked_row, "category_name", "") or ""
                ).strip()
                if linked_category != "未分類":
                    raise PalletError(
                        "この登録には分類済みパレットが混在しています。"
                        "安全のため強制整理できません。"
                    )

            removed_qty = sum(
                int(row_value(row, "current_qty", 0) or 0)
                for row in linked_rows
            )

            if current_batch_code:
                conn.execute(
                    """
                    UPDATE pallet_inventory
                    SET is_deleted = TRUE, updated_at = ?
                    WHERE
                        batch_code = ?
                        AND COALESCE(is_deleted, FALSE) = FALSE
                    """,
                    (now, current_batch_code),
                )

            conn.execute(
                """
                UPDATE pallet_receiving_plans
                SET
                    status = '取消',
                    is_deleted = TRUE,
                    updated_at = ?
                WHERE
                    id = ?
                    AND COALESCE(is_deleted, FALSE) = FALSE
                """,
                (now, source_id),
            )

            conn.commit()

            return (
                f"未分類の旧登録を整理しました。"
                f" {len(linked_rows)}パレット / {removed_qty:,}個を"
                "現在庫対象から除外しました。"
                " 入出庫履歴データ自体は残しています。"
            )

        if source_type == "inventory":
            rows = inventory_rows_for_batch(batch_code, source_id)

            if not rows:
                raise PalletError("対象の未分類在庫が見つかりません。")

            for row in rows:
                category_name = str(
                    row_value(row, "category_name", "") or ""
                ).strip()
                if category_name != "未分類":
                    raise PalletError(
                        "この登録には分類済みパレットが混在しています。"
                        "安全のため強制整理できません。"
                    )

            removed_qty = sum(
                int(row_value(row, "current_qty", 0) or 0)
                for row in rows
            )

            if batch_code:
                conn.execute(
                    """
                    UPDATE pallet_inventory
                    SET is_deleted = TRUE, updated_at = ?
                    WHERE
                        batch_code = ?
                        AND COALESCE(is_deleted, FALSE) = FALSE
                    """,
                    (now, batch_code),
                )
                conn.execute(
                    """
                    UPDATE pallet_receiving_plans
                    SET
                        status = '取消',
                        is_deleted = TRUE,
                        updated_at = ?
                    WHERE
                        batch_code = ?
                        AND COALESCE(is_deleted, FALSE) = FALSE
                    """,
                    (now, batch_code),
                )
            else:
                conn.execute(
                    """
                    UPDATE pallet_inventory
                    SET is_deleted = TRUE, updated_at = ?
                    WHERE
                        id = ?
                        AND COALESCE(is_deleted, FALSE) = FALSE
                    """,
                    (now, source_id),
                )

            conn.commit()

            return (
                f"未分類の旧在庫を整理しました。"
                f" {len(rows)}パレット / {removed_qty:,}個を"
                "現在庫対象から除外しました。"
                " 入出庫履歴データ自体は残しています。"
            )

        raise PalletError("未分類データの区分が不正です。")

    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


# === SHARK UNCLASSIFIED CLEANUP HELPERS END ===


# ============================================================
# 4つだけの通常画面
# ============================================================
tab_register, tab_qr, tab_stock, tab_daily, tab_history = st.tabs(
    ["登録・印刷", "QR入出庫", "現在庫", "日次集計", "履歴"]
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
        st.subheader("登録完了／A4印刷")
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
        st.info(
            "A4は登録したパレット枚数と同じページ数です。"
            "1パレットにつき1ページを印刷してください。"
        )

        if st.button(
            "次の商品を登録する",
            use_container_width=True,
        ):
            st.session_state.simple_plan_pdf = None
            st.session_state.simple_plan_pdf_name = ""
            st.session_state.simple_plan_code = ""
            st.rerun()

        # 登録後は入力フォームを再表示せず、
        # A4のダウンロード／印刷に集中させる。
    else:
        # QRタブ白画面修正：A4表示中は登録フォームだけを省略する。
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
            # 業者と登録済み商品の選択はフォーム外に置く。
            # これにより、業者を変えた瞬間に商品候補を絞り込める。
            company_label = st.selectbox(
                "業者名",
                options=list(company_map.keys()),
                key="simple_plan_company",
            )
            selected_company = company_map[company_label]

            registered_items = get_items_for_company(
                conn,
                selected_company["id"],
            )
            new_item_option = "＋ 新しい商品を直接入力"
            registered_item_map = {}

            for item_index, item in enumerate(registered_items, start=1):
                item_code = str(row_value(item, "code", "") or "").strip()
                item_name = str(row_value(item, "name", "") or "").strip()
                category_name = str(
                    row_value(item, "category_name", "未分類") or "未分類"
                ).strip()
                code_display = item_code or "コードなし"
                option_label = (
                    f"{code_display}｜{category_name}｜{item_name}"
                )

                # 同名・同コードの履歴が万一残っていても、selectboxの
                # 選択肢が重複しないようにする。
                if option_label in registered_item_map:
                    option_label = f"{option_label}（{item_index}）"

                registered_item_map[option_label] = item

            item_options = [new_item_option] + list(
                registered_item_map.keys()
            )
            selected_item_label = st.selectbox(
                "登録する商品",
                options=item_options,
                key=(
                    "simple_plan_registered_item_"
                    f"{selected_company['id']}"
                ),
            )
            selected_registered_item = registered_item_map.get(
                selected_item_label
            )

            if registered_items:
                st.caption(
                    f"この業者の登録済み商品：{len(registered_items):,}件"
                )
            else:
                st.info(
                    "この業者には登録済み商品がありません。"
                    "新しい商品を入力してください。"
                )

            form_version = st.session_state.simple_plan_form_version

            with st.form(f"simple_receiving_plan_form_{form_version}"):
                receiving_date_value = st.date_input(
                    "入庫予定日",
                    value=date.today(),
                )
                new_category_code_value = ""

                if selected_registered_item is not None:
                    selected_item_id = row_value(
                        selected_registered_item,
                        "item_id",
                        row_value(selected_registered_item, "id"),
                    )
                    selected_project_id = row_value(
                        selected_registered_item,
                        "project_id",
                    )
                    selected_category_id = row_value(
                        selected_registered_item,
                        "category_id",
                    )
                    selected_category_name = str(
                        row_value(
                            selected_registered_item,
                            "category_name",
                            "未分類",
                        )
                        or "未分類"
                    ).strip()
                    item_code_value = str(
                        row_value(selected_registered_item, "code", "") or ""
                    ).strip()
                    item_name_value = str(
                        row_value(selected_registered_item, "name", "") or ""
                    ).strip()

                    st.text_input(
                        "大カテゴリー",
                        value=selected_category_name,
                        disabled=True,
                    )
                    item_col1, item_col2 = st.columns(2)

                    with item_col1:
                        st.text_input(
                            "商品コード",
                            value=item_code_value,
                            disabled=True,
                        )

                    with item_col2:
                        st.text_input(
                            "商品名",
                            value=item_name_value,
                            disabled=True,
                        )

                    st.caption(
                        "登録済みの商品情報を自動入力しました。"
                        "変更する場合は「新しい商品を直接入力」を選んでください。"
                    )

                else:
                    selected_item_id = None
                    selected_project_id = None
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

                    new_category_code_value = st.text_input(
                        "新しい大カテゴリーコード（新規のときだけ）",
                        value="",
                        placeholder="例：KAG",
                        help="英数字2～8文字。商品コードの2番目に使用します。",
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

                    normalized_new_category = str(
                        new_category_name or ""
                    ).strip()

                    if normalized_new_category:
                        selected_category_id = None
                        selected_category_name = normalized_new_category
                    else:
                        selected_category_id = category_map[category_label]
                        selected_category_name = category_label

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

                if not normalized_item_name:
                    st.error("商品名を入力してください。")
                else:
                    try:
                        receipt_code = create_receiving_plan(
                            conn=conn,
                            receiving_date=receiving_date_value,
                            company_id=selected_company["id"],
                            project_id=selected_project_id,
                            item_id=selected_item_id,
                            pallet_count=int(pallet_count),
                            qty_per_pallet=int(qty_per_pallet),
                            username=username,
                            item_code=str(item_code_value or "").strip(),
                            item_name=normalized_item_name,
                            category_id=selected_category_id,
                            category_name=selected_category_name,
                            category_code=new_category_code_value,
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

    if st.session_state.simple_qr_flash:
        st.success(st.session_state.simple_qr_flash)
        st.session_state.simple_qr_flash = ""

    operation_type = st.radio(
        "処理",
        options=["入庫", "出庫"],
        horizontal=True,
        key="simple_qr_operation",
    )

    if operation_type == "入庫":
        st.caption(
            "A4のQRを1枚ずつ読み取り、"
            "読み取ったパレットだけ入庫確定します。"
        )

        with st.form(
            "simple_qr_receiving_form",
            clear_on_submit=True,
        ):
            qr_code = st.text_input(
                "QRコード",
                value="",
                placeholder="QRを読み取ってEnter",
            )
            qr_submitted = st.form_submit_button(
                "読み取って入庫",
                type="primary",
                use_container_width=True,
            )

        if qr_submitted:
            normalized_code = str(
                qr_code or ""
            ).strip().upper()

            if not normalized_code:
                st.warning("QRコードを読み取ってください。")
            else:
                try:
                    result = confirm_receiving_plan(
                        conn=conn,
                        receipt_or_pallet_code=normalized_code,
                        username=username,
                    )
                    st.session_state.simple_qr_flash = (
                        "パレットナンバー "
                        f"{int(result['category_sequence']):03d}　"
                        f"{int(result['received_qty']):,}個 入庫確定"
                        + (
                            f"（全{int(result['pallet_count']):,}枚"
                            "の入庫完了）"
                            if result["completed"]
                            else (
                                "（残り"
                                f"{int(result['remaining_pallets']):,}枚）"
                            )
                        )
                    )
                    st.rerun()

                except PalletError as exc:
                    st.error(str(exc))

                except Exception as exc:
                    st.error(
                        f"入庫中にエラーが発生しました：{exc}"
                    )

    else:
        st.caption(
            "①QR読取 → ②今回分を確認 → "
            "③出庫確定 → ④納品書・受領書"
        )
        st.info(
            "QRを読み取っただけでは在庫は減りません。"
            "最後の「出庫確定」で今回分を一括処理します。"
        )

        documents = (
            st.session_state.simple_pallet_out_documents
        )

        if documents:
            st.success(
                "今回のパレット出庫が完了しました。"
                "納品書・受領書を保存してください。"
            )

            confirmed_rows = documents["rows"]
            confirmed_total = sum(
                int(row["quantity"])
                for row in confirmed_rows
            )

            metric1, metric2, metric3 = st.columns(3)
            metric1.metric(
                "荷主",
                documents["company_name"] or "-",
            )
            metric2.metric(
                "パレット",
                f"{len(confirmed_rows):,}パレ",
            )
            metric3.metric(
                "出庫数量",
                f"{confirmed_total:,}個",
            )

            doc_col1, doc_col2 = st.columns(2)

            with doc_col1:
                st.download_button(
                    "📄 納品書をダウンロード",
                    data=documents["delivery"],
                    file_name=(
                        "納品書_パレット_"
                        f"{documents['stamp']}.pdf"
                    ),
                    mime="application/pdf",
                    use_container_width=True,
                )

            with doc_col2:
                st.download_button(
                    "📄 受領書をダウンロード",
                    data=documents["receipt"],
                    file_name=(
                        "受領書_パレット_"
                        f"{documents['stamp']}.pdf"
                    ),
                    mime="application/pdf",
                    use_container_width=True,
                )

            if st.button(
                "🧹 次の出庫を開始",
                key="simple_pallet_out_next",
                use_container_width=True,
            ):
                st.session_state.simple_pallet_out_rows = []
                st.session_state.simple_pallet_out_documents = None
                st.session_state.simple_pallet_out_company_id = None
                st.session_state.simple_pallet_out_company_name = ""
                st.session_state.simple_qr_flash = (
                    "新しいパレット出庫を開始しました。"
                )
                st.rerun()

        else:
            form_version = (
                st.session_state.simple_pallet_out_form_version
            )

            with st.form(
                f"simple_pallet_out_scan_{form_version}",
                clear_on_submit=True,
            ):
                shipping_quantity_text = st.text_input(
                    "出庫数（空欄ならパレット全数）",
                    value="",
                    placeholder="分納するときだけ入力",
                )
                qr_code = st.text_input(
                    "QRコード",
                    value="",
                    placeholder="QRを読み取ってEnter",
                )
                qr_submitted = st.form_submit_button(
                    "今回の出庫に追加",
                    type="primary",
                    use_container_width=True,
                )

            if qr_submitted:
                normalized_code = str(
                    qr_code or ""
                ).strip().upper()

                if not normalized_code:
                    st.warning("QRコードを読み取ってください。")
                else:
                    try:
                        pallet = get_pallet_by_code(
                            conn=conn,
                            pallet_code=normalized_code,
                        )

                        if pallet is None:
                            raise PalletError(
                                "パレットが見つかりません。"
                                "QRを確認してください。"
                            )

                        current_qty = int(
                            row_value(
                                pallet,
                                "current_qty",
                                0,
                            )
                        )

                        if current_qty <= 0:
                            raise PalletError(
                                "このパレットは"
                                "すでに出庫済みです。"
                            )

                        quantity_text = str(
                            shipping_quantity_text or ""
                        ).replace(",", "").strip()

                        if quantity_text:
                            try:
                                shipping_qty = int(
                                    quantity_text
                                )
                            except ValueError as exc:
                                raise PalletError(
                                    "出庫数は整数で"
                                    "入力してください。"
                                ) from exc
                        else:
                            shipping_qty = current_qty

                        if shipping_qty <= 0:
                            raise PalletError(
                                "出庫数は1以上で"
                                "入力してください。"
                            )

                        if shipping_qty > current_qty:
                            raise PalletError(
                                f"現在庫は {current_qty:,}個です。"
                            )

                        staged_rows = list(
                            st.session_state.simple_pallet_out_rows
                        )

                        if any(
                            row["pallet_code"] == normalized_code
                            for row in staged_rows
                        ):
                            raise PalletError(
                                "このパレットは今回の出庫に"
                                "すでに追加されています。"
                            )

                        company_id = int(
                            row_value(
                                pallet,
                                "company_id",
                                0,
                            )
                            or 0
                        )
                        company_name = str(
                            row_value(
                                pallet,
                                "company_name",
                                "",
                            )
                            or ""
                        ).strip()

                        selected_company_id = (
                            st.session_state
                            .simple_pallet_out_company_id
                        )

                        if (
                            staged_rows
                            and selected_company_id
                            not in (None, company_id)
                        ):
                            raise PalletError(
                                "荷主が違います。"
                                "1枚の納品書・受領書に"
                                "別荷主は混在できません。"
                            )

                        staged_rows.append(
                            {
                                "pallet_code": normalized_code,
                                "quantity": shipping_qty,
                                "company_id": company_id,
                                "company_name": company_name,
                                "category_sequence": int(
                                    row_value(
                                        pallet,
                                        "category_sequence",
                                        0,
                                    )
                                    or 0
                                ),
                                "category_name": str(
                                    row_value(
                                        pallet,
                                        "category_name",
                                        "",
                                    )
                                    or ""
                                ).strip(),
                                "item_code": str(
                                    row_value(
                                        pallet,
                                        "item_code",
                                        "",
                                    )
                                    or ""
                                ).strip(),
                                "item_name": str(
                                    row_value(
                                        pallet,
                                        "item_name",
                                        "",
                                    )
                                    or ""
                                ).strip(),
                                "current_qty": current_qty,
                            }
                        )

                        st.session_state.simple_pallet_out_rows = (
                            staged_rows
                        )
                        st.session_state.simple_pallet_out_company_id = (
                            company_id
                        )
                        st.session_state.simple_pallet_out_company_name = (
                            company_name
                        )
                        st.session_state.simple_pallet_out_form_version += 1
                        st.session_state.simple_qr_flash = (
                            f"管理番号 "
                            f"{int(row_value(pallet, 'category_sequence', 0)):03d} "
                            f"{shipping_qty:,}個を今回の出庫に追加しました。"
                        )
                        st.rerun()

                    except PalletError as exc:
                        st.error(str(exc))

                    except Exception as exc:
                        st.error(
                            "QR確認中にエラーが"
                            f"発生しました：{exc}"
                        )

            staged_rows = list(
                st.session_state.simple_pallet_out_rows
            )

            if staged_rows:
                st.divider()
                st.subheader("今回の出庫")

                staged_total = sum(
                    int(row["quantity"])
                    for row in staged_rows
                )

                metric1, metric2, metric3 = st.columns(3)
                metric1.metric(
                    "荷主",
                    st.session_state
                    .simple_pallet_out_company_name
                    or "-",
                )
                metric2.metric(
                    "読取パレット",
                    f"{len(staged_rows):,}パレ",
                )
                metric3.metric(
                    "出庫数量",
                    f"{staged_total:,}個",
                )

                staged_df = pd.DataFrame(
                    [
                        {
                            "管理番号": (
                                f"{int(row['category_sequence']):03d}"
                                if int(row["category_sequence"]) > 0
                                else ""
                            ),
                            "大カテゴリー": row["category_name"],
                            "商品コード": row["item_code"],
                            "商品名": row["item_name"],
                            "出庫数": int(row["quantity"]),
                        }
                        for row in staged_rows
                    ]
                )

                st.dataframe(
                    staged_df,
                    hide_index=True,
                    use_container_width=True,
                )

                cancel_col, reset_col = st.columns(2)

                with cancel_col:
                    if st.button(
                        "↩️ 直前の読取を取り消す",
                        key="simple_pallet_out_undo",
                        use_container_width=True,
                    ):
                        updated_rows = list(
                            st.session_state.simple_pallet_out_rows
                        )
                        updated_rows.pop()
                        st.session_state.simple_pallet_out_rows = (
                            updated_rows
                        )

                        if not updated_rows:
                            st.session_state.simple_pallet_out_company_id = (
                                None
                            )
                            st.session_state.simple_pallet_out_company_name = (
                                ""
                            )

                        st.rerun()

                with reset_col:
                    if st.button(
                        "🗑 読取をすべてリセット",
                        key="simple_pallet_out_reset",
                        use_container_width=True,
                    ):
                        st.session_state.simple_pallet_out_rows = []
                        st.session_state.simple_pallet_out_company_id = None
                        st.session_state.simple_pallet_out_company_name = ""
                        st.rerun()

                st.write("---")

                if st.button(
                    (
                        "✅ "
                        f"{len(staged_rows):,}パレット / "
                        f"{staged_total:,}個を出庫確定"
                    ),
                    type="primary",
                    key="simple_pallet_out_confirm",
                    use_container_width=True,
                ):
                    try:
                        delivery_pdf = (
                            create_pallet_shipment_document(
                                "納品書",
                                staged_rows,
                                issued_by=display_name,
                            )
                        )
                        receipt_pdf = (
                            create_pallet_shipment_document(
                                "受領書",
                                staged_rows,
                                issued_by=display_name,
                            )
                        )

                        results = ship_pallet_batch(
                            conn=conn,
                            shipments=staged_rows,
                            username=username,
                        )

                        stamp = datetime.now(JST).strftime(
                            "%Y%m%d_%H%M%S"
                        )

                        st.session_state.simple_pallet_out_documents = {
                            "delivery": delivery_pdf,
                            "receipt": receipt_pdf,
                            "rows": staged_rows,
                            "company_name": (
                                st.session_state
                                .simple_pallet_out_company_name
                            ),
                            "stamp": stamp,
                        }
                        st.session_state.simple_pallet_out_rows = []
                        st.session_state.simple_qr_flash = (
                            f"{len(results):,}パレットを"
                            "出庫確定しました。"
                        )
                        st.rerun()

                    except PalletError as exc:
                        st.error(str(exc))

                    except Exception as exc:
                        st.error(
                            "出庫確定中にエラーが"
                            f"発生しました：{exc}"
                        )
            else:
                st.info(
                    "まだパレットを読み取っていません。"
                )

# ============================================================
# 3. 現在庫
# ============================================================
with tab_stock:
    st.subheader("現在庫")
    st.caption(
        "今この時点で保管しているパレットを、荷主・大カテゴリー別に表示します。"
    )

    stock_search = st.text_input(
        "検索",
        placeholder=(
            "荷主・大カテゴリー・商品コード・商品名・管理番号"
        ),
        key="simple_stock_search",
    )

    all_stock_rows = list_pallet_stock(
        conn=conn,
        status="保管中",
        search_text="",
    )
    stock_rows = [
        row
        for row in all_stock_rows
        if stock_row_matches(row, stock_search)
    ]

    stock_summary_df = company_category_stock_dataframe(stock_rows)
    total_current_qty = sum(
        int(row_value(row, "current_qty", 0))
        for row in stock_rows
    )
    company_count = len(
        {
            str(row_value(row, "company_name", "") or "荷主未設定")
            for row in stock_rows
        }
    )

    metric_col1, metric_col2, metric_col3 = st.columns(3)

    with metric_col1:
        st.metric("荷主数", f"{company_count:,}社")

    with metric_col2:
        st.metric("現在パレット", f"{len(stock_rows):,}パレ")

    with metric_col3:
        st.metric("現在庫合計", f"{total_current_qty:,}個")

    if not stock_rows:
        st.info("該当する現在庫はありません。")
    else:
        st.subheader("荷主・大カテゴリー別")
        st.dataframe(
            stock_summary_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "現在パレ": st.column_config.NumberColumn(
                    "現在パレ",
                    format="%d パレ",
                ),
                "現在個数": st.column_config.NumberColumn(
                    "現在個数",
                    format="%d 個",
                ),
            },
        )

        st.divider()
        st.subheader("パレット内訳")
        st.caption(
            "履歴ではなく、現在保管しているパレットだけを表示します。"
        )

        company_options = sorted(
            {
                str(
                    row_value(row, "company_name", "")
                    or "荷主未設定"
                ).strip()
                for row in stock_rows
            }
        )

        selected_company = st.selectbox(
            "荷主",
            options=company_options,
            key="simple_stock_company",
        )

        company_rows = [
            row
            for row in stock_rows
            if str(
                row_value(row, "company_name", "")
                or "荷主未設定"
            ).strip()
            == selected_company
        ]

        category_options = sorted(
            {
                str(
                    row_value(row, "category_name", "")
                    or "未分類"
                ).strip()
                for row in company_rows
            }
        )

        selected_category = st.selectbox(
            "大カテゴリー",
            options=category_options,
            key="simple_stock_category",
        )

        detail_rows = [
            row
            for row in company_rows
            if str(
                row_value(row, "category_name", "")
                or "未分類"
            ).strip()
            == selected_category
        ]

        detail_col1, detail_col2 = st.columns(2)

        with detail_col1:
            st.metric(
                f"{selected_company} / {selected_category}",
                f"{len(detail_rows):,}パレ",
            )

        with detail_col2:
            st.metric(
                "商品個数",
                (
                    f"{sum(int(row_value(row, 'current_qty', 0)) for row in detail_rows):,}"
                    "個"
                ),
            )

        st.dataframe(
            compact_pallet_dataframe(detail_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "個数": st.column_config.NumberColumn(
                    "個数",
                    format="%d 個",
                ),
            },
        )


# ============================================================
# 4. 日次集計
# ============================================================
with tab_daily:
    st.subheader("日次集計")
    st.caption(
        "前日19:00超～当日19:00までを1日として集計します。"
        "今日が19:00前なら現在時刻までをLIVE表示します。"
    )

    daily_days = st.selectbox(
        "表示期間",
        options=[7, 14, 30],
        index=1,
        format_func=lambda value: f"直近 {value} 日",
        key="simple_daily_days",
    )

    daily_history_rows = list_pallet_history(
        conn=conn,
        history_type="すべて",
        limit=50000,
    )
    daily_df = daily_pallet_summary_dataframe(
        daily_history_rows,
        days=int(daily_days),
        now=datetime.now(JST),
    )

    today_text = datetime.now(JST).date().strftime("%Y-%m-%d")
    today_df = (
        daily_df[daily_df["締め日"] == today_text]
        if not daily_df.empty
        else pd.DataFrame()
    )

    daily_metric1, daily_metric2, daily_metric3 = st.columns(3)

    with daily_metric1:
        st.metric(
            "本日入庫",
            (
                f"{int(today_df['入庫パレ'].sum()):,}パレ"
                if not today_df.empty
                else "0パレ"
            ),
        )

    with daily_metric2:
        st.metric(
            "本日出庫",
            (
                f"{int(today_df['出庫パレ'].sum()):,}パレ"
                if not today_df.empty
                else "0パレ"
            ),
        )

    with daily_metric3:
        st.metric(
            "現在 / 19時時点",
            (
                f"{int(today_df['時点在庫パレ'].sum()):,}パレ"
                if not today_df.empty
                else "0パレ"
            ),
        )

    if daily_df.empty:
        st.info("日次集計できる履歴がありません。")
    else:
        st.dataframe(
            daily_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "入庫パレ": st.column_config.NumberColumn(
                    "入庫パレ",
                    format="%d パレ",
                ),
                "出庫パレ": st.column_config.NumberColumn(
                    "出庫パレ",
                    format="%d パレ",
                ),
                "時点在庫パレ": st.column_config.NumberColumn(
                    "時点在庫パレ",
                    format="%d パレ",
                ),
            },
        )


# ============================================================
# 5. 履歴
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
    with st.expander(
        "管理メニュー",
        expanded=bool(st.session_state.simple_admin_action),
    ):
        st.caption(
            "通常の入出庫では使いません。必要な管理操作だけ選んでください。"
        )

        admin_col1, admin_col2, admin_col3, admin_col4 = st.columns(4)

        with admin_col1:
            if st.button(
                "🏷️ 商品コード管理",
                key="simple_admin_menu_item_code",
                type=(
                    "primary"
                    if st.session_state.simple_admin_action
                    == "商品コード管理"
                    else "secondary"
                ),
                use_container_width=True,
            ):
                st.session_state.simple_admin_action = "商品コード管理"
                st.rerun()

        with admin_col2:
            if st.button(
                "🔢 採番管理",
                key="simple_admin_menu_numbering",
                type=(
                    "primary"
                    if st.session_state.simple_admin_action
                    == "採番管理"
                    else "secondary"
                ),
                use_container_width=True,
            ):
                st.session_state.simple_admin_action = "採番管理"
                st.rerun()

        with admin_col3:
            if st.button(
                "🖨️ 入庫待ち",
                key="simple_admin_menu_pending",
                type=(
                    "primary"
                    if st.session_state.simple_admin_action
                    == "入庫待ち"
                    else "secondary"
                ),
                use_container_width=True,
            ):
                st.session_state.simple_admin_action = "入庫待ち"
                st.rerun()

        with admin_col4:
            if st.button(
                "✏️ 登録内容修正",
                key="simple_admin_menu_edit",
                type=(
                    "primary"
                    if st.session_state.simple_admin_action
                    == "登録内容修正"
                    else "secondary"
                ),
                use_container_width=True,
            ):
                st.session_state.simple_admin_action = "登録内容修正"
                st.rerun()

        if not st.session_state.simple_admin_action:
            st.info(
                "上のボタンから、やりたい管理操作を選んでください。"
            )
        else:
            st.caption(
                f"選択中：{st.session_state.simple_admin_action}"
            )

        if st.session_state.simple_admin_action == "商品コード管理":
            st.subheader("🏷️ 商品コード管理")
            st.caption(
                "大カテゴリーコード設定と既存商品の商品コード採番を行います。"
            )
            st.divider()
            st.subheader("🧹 未分類データ整理")
            st.caption(
                "商品コード管理に残る未分類データの元を確認して、"
                "実在庫が無いものだけ手動で取消できます。"
            )

            try:
                cleanup_candidates = (
                    list_unclassified_cleanup_candidates(conn)
                )

                if not cleanup_candidates:
                    st.success(
                        "整理が必要な未分類データはありません。"
                    )
                else:
                    cleanup_df = pd.DataFrame(
                        [
                            {
                                "区分": row["区分"],
                                "管理番号": row["管理番号"],
                                "状態": row["状態"],
                                "荷主": row["荷主"],
                                "商品コード": row["商品コード"],
                                "商品名": row["商品名"],
                                "パレット数": row["パレット数"],
                                "現在個数": row["現在個数"],
                                "登録日時": row["登録日時"],
                                "取消可否": (
                                    "取消可"
                                    if row["can_cancel"]
                                    else "保護"
                                ),
                                "理由": row["理由"],
                            }
                            for row in cleanup_candidates
                        ]
                    )
                    st.dataframe(
                        cleanup_df,
                        hide_index=True,
                        use_container_width=True,
                    )

                    cleanup_map = {
                        (
                            f"{row['区分']}｜{row['管理番号']}｜"
                            f"{row['荷主']}｜{row['商品名']}｜"
                            f"{row['状態']}"
                        ): row
                        for row in cleanup_candidates
                    }

                    selected_cleanup_label = st.selectbox(
                        "手動取消するデータ",
                        options=list(cleanup_map.keys()),
                        key="pallet_unclassified_cleanup_target",
                    )
                    selected_cleanup = cleanup_map[
                        selected_cleanup_label
                    ]

                    if selected_cleanup["can_cancel"]:
                        with st.form(
                            "pallet_unclassified_cleanup_form"
                        ):
                            cleanup_confirmed = st.checkbox(
                                "この未分類データを手動取消する"
                            )
                            cleanup_submitted = (
                                st.form_submit_button(
                                    "未分類データを取消",
                                    type="primary",
                                    use_container_width=True,
                                )
                            )

                        if cleanup_submitted:
                            if not cleanup_confirmed:
                                st.warning(
                                    "確認にチェックを入れてください。"
                                )
                            else:
                                try:
                                    cleanup_message = (
                                        cleanup_unclassified_candidate(
                                            conn,
                                            selected_cleanup,
                                        )
                                    )
                                    st.session_state.simple_admin_flash = (
                                        cleanup_message
                                    )
                                    st.rerun()
                                except PalletError as exc:
                                    st.error(str(exc))
                                except Exception as exc:
                                    st.error(
                                        "未分類データの取消中に"
                                        f"エラーが発生しました：{exc}"
                                    )
                    else:
                        st.warning(
                            "この未分類データには現在庫が残っています。"
                            "旧・二重登録だと確認できる場合だけ強制整理できます。"
                        )

                        st.caption(
                            "強制整理すると、この未分類側を現在庫対象から除外します。"
                            "分類済みパレットが混在している場合は自動で中止します。"
                        )

                        with st.form(
                            "pallet_unclassified_force_cleanup_form"
                        ):
                            force_confirm_text = st.text_input(
                                "確認のため商品名を入力",
                                key="pallet_unclassified_force_confirm",
                            )
                            force_submitted = st.form_submit_button(
                                "⚠️ この未分類データを強制整理",
                                type="primary",
                                use_container_width=True,
                            )

                        if force_submitted:
                            expected_name = str(
                                selected_cleanup["商品名"] or ""
                            ).strip()

                            if force_confirm_text.strip() != expected_name:
                                st.warning(
                                    "商品名が一致しません。"
                                    "対象の商品名をそのまま入力してください。"
                                )
                            else:
                                try:
                                    cleanup_message = (
                                        force_cleanup_unclassified_candidate(
                                            conn,
                                            selected_cleanup,
                                        )
                                    )
                                    st.session_state.simple_admin_flash = (
                                        cleanup_message
                                    )
                                    st.rerun()
                                except PalletError as exc:
                                    st.error(str(exc))
                                except Exception as exc:
                                    st.error(
                                        "未分類データの強制整理中に"
                                        f"エラーが発生しました：{exc}"
                                    )

            except Exception as exc:
                st.error(
                    "未分類データの確認中にエラーが発生しました："
                    f"{exc}"
                )

            st.caption(
                "商品コード形式：顧客コード-大カテゴリーコード-"
                "初回登録年月-4桁連番"
            )

            try:
                category_settings = list_pallet_category_code_settings(conn)

                if not category_settings:
                    st.info("大カテゴリーがありません。")
                else:
                    category_setting_map = {
                        (
                            f"{row['category_name']}｜"
                            f"{row.get('category_code') or '未設定'}"
                        ): row
                        for row in category_settings
                    }
                    selected_setting_label = st.selectbox(
                        "コードを設定する大カテゴリー",
                        options=list(category_setting_map.keys()),
                        key="pallet_item_category_code_target",
                    )
                    selected_setting = category_setting_map[
                        selected_setting_label
                    ]

                    with st.form("pallet_item_category_code_form"):
                        category_code_input = st.text_input(
                            "大カテゴリーコード",
                            value=str(
                                selected_setting.get("category_code") or ""
                            ),
                            placeholder="例：KAG",
                            help="英数字2～8文字",
                        )
                        category_code_submitted = st.form_submit_button(
                            "大カテゴリーコードを保存",
                            use_container_width=True,
                        )

                    if category_code_submitted:
                        set_pallet_category_code(
                            conn=conn,
                            category_id=selected_setting["category_id"],
                            category_code=category_code_input,
                            username=username,
                        )
                        st.success("大カテゴリーコードを保存しました。")
                        st.rerun()

                st.divider()
                st.write("現行登録済み商品の採番プレビュー")
                existing_preview = (
                    preview_existing_pallet_item_numbering(conn)
                )

                if not existing_preview:
                    st.info("採番対象の商品データはありません。")
                else:
                    preview_df = pd.DataFrame(existing_preview)
                    st.dataframe(
                        preview_df,
                        hide_index=True,
                        use_container_width=True,
                    )
                    applicable_count = sum(
                        row["状態"] in {
                            "採番対象",
                            "既存コードを統一",
                            "変更なし",
                        }
                        for row in existing_preview
                    )
                    problem_count = len(existing_preview) - applicable_count
                    st.caption(
                        f"処理可能：{applicable_count:,}商品 ／ "
                        f"要確認：{problem_count:,}商品"
                    )

                    with st.form(
                        "apply_existing_pallet_item_numbering_form"
                    ):
                        apply_confirmed = st.checkbox(
                            "プレビュー内容を確認し、既存商品へ商品コードを反映する"
                        )
                        apply_submitted = st.form_submit_button(
                            "既存商品の採番を実行",
                            type="primary",
                            use_container_width=True,
                        )

                    if apply_submitted:
                        if not apply_confirmed:
                            st.warning("確認にチェックを入れてください。")
                        else:
                            result = apply_existing_pallet_item_numbering(
                                conn,
                                username=username,
                            )
                            st.success(
                                "既存商品の採番が完了しました。"
                                f" 対象商品：{result['product_count']:,}件、"
                                f" 更新データ：{result['updated_rows']:,}件、"
                                f" 要確認：{result['skipped_count']:,}件"
                            )
                            st.rerun()

            except PalletItemNumberingError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(
                    "商品コード管理中にエラーが発生しました："
                    f"{exc}"
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

        if st.session_state.simple_admin_action == "採番管理":
            st.subheader("🔢 採番管理")
            st.caption(
                "登録済み管理番号と次回開始番号を変更します。"
            )
            st.caption(
                "管理番号だけを変更します。QRコードは変わりません。"
                "複数パレットは開始番号から連番のまま移動します。"
            )

            st.write("登録済み番号を変更")
            numbering_registrations = list_pallet_numbering_registrations(
                conn,
                limit=1000,
            )

            if not numbering_registrations:
                st.info("採番変更できる登録はありません。")
            else:
                numbering_registration_map = {
                    numbering_registration_label(registration): registration
                    for registration in numbering_registrations
                }
                selected_numbering_label = st.selectbox(
                    "番号を変更する登録",
                    options=list(numbering_registration_map.keys()),
                    key="simple_numbering_registration",
                )
                selected_numbering = numbering_registration_map[
                    selected_numbering_label
                ]
                current_start_sequence = int(
                    row_value(
                        selected_numbering,
                        "category_start_sequence",
                        1,
                    )
                )
                selected_pallet_count = int(
                    row_value(selected_numbering, "pallet_count", 1)
                )
                current_end_sequence = current_start_sequence + (
                    selected_pallet_count - 1
                )
                source_type = str(
                    row_value(selected_numbering, "source_type", "")
                )
                registration_id = int(
                    row_value(selected_numbering, "registration_id", 0)
                )

                number_info_col1, number_info_col2 = st.columns(2)

                with number_info_col1:
                    st.metric(
                        "現在の番号",
                        (
                            f"{current_start_sequence:03d}"
                            if current_start_sequence
                            == current_end_sequence
                            else (
                                f"{current_start_sequence:03d}～"
                                f"{current_end_sequence:03d}"
                            )
                        ),
                    )

                with number_info_col2:
                    st.metric(
                        "パレット枚数",
                        f"{selected_pallet_count:,}枚",
                    )

                with st.form(
                    "simple_numbering_renumber_form_"
                    f"{source_type}_{registration_id}"
                ):
                    new_start_sequence = st.number_input(
                        "変更後の開始番号",
                        min_value=1,
                        max_value=999_999,
                        value=current_start_sequence,
                        step=1,
                    )
                    renumber_confirmed = st.checkbox(
                        "番号変更後にA4を再印刷する"
                    )
                    renumber_submitted = st.form_submit_button(
                        "登録済み番号を変更",
                        type="primary",
                        use_container_width=True,
                    )

                if renumber_submitted:
                    if not renumber_confirmed:
                        st.warning("確認にチェックを入れてください。")
                    else:
                        try:
                            renumber_result = renumber_pallet_registration(
                                conn=conn,
                                source_type=source_type,
                                registration_id=registration_id,
                                batch_code=row_value(
                                    selected_numbering,
                                    "batch_code",
                                    "",
                                ),
                                new_start_sequence=int(new_start_sequence),
                            )

                            if renumber_result["source_type"] == "batch":
                                renumbered_rows = get_batch_pallets(
                                    conn,
                                    renumber_result["batch_code"],
                                )
                                st.session_state.simple_admin_pdf = (
                                    create_pallet_a4_pdf(renumbered_rows)
                                )
                                st.session_state.simple_admin_pdf_name = (
                                    "pallet_"
                                    f"{renumber_result['batch_code']}.pdf"
                                )
                            else:
                                renumbered_plan = (
                                    get_receiving_plan_by_code(
                                        conn,
                                        renumber_result["receipt_code"],
                                    )
                                )
                                st.session_state.simple_admin_pdf = (
                                    create_receiving_plan_a4_pdf(
                                        [renumbered_plan]
                                    )
                                )
                                st.session_state.simple_admin_pdf_name = (
                                    "receiving_"
                                    f"{renumber_result['receipt_code']}.pdf"
                                )

                            st.session_state.simple_admin_flash = (
                                "管理番号を "
                                f"{renumber_result['new_start_sequence']:03d}"
                                + (
                                    ""
                                    if renumber_result[
                                        "new_start_sequence"
                                    ]
                                    == renumber_result["new_end_sequence"]
                                    else (
                                        "～"
                                        f"{renumber_result['new_end_sequence']:03d}"
                                    )
                                )
                                + " に変更しました。A4を再印刷してください。"
                            )
                            st.rerun()

                        except PalletError as exc:
                            st.error(str(exc))

                        except Exception as exc:
                            st.error(
                                "採番変更中にエラーが発生しました："
                                f"{exc}"
                            )

            st.divider()
            st.write("次回の採番開始番号を変更")
            numbering_categories = list_pallet_categories(
                conn,
                include_hidden=True,
            )

            if not numbering_categories:
                st.info("大カテゴリーがありません。")
            else:
                numbering_category_map = {
                    (
                        f"{row_value(category, 'name', '')}｜"
                        "現在の次回番号 "
                        f"{int(row_value(category, 'next_sequence', 1)):03d}"
                    ): category
                    for category in numbering_categories
                }
                selected_category_label = st.selectbox(
                    "大カテゴリー",
                    options=list(numbering_category_map.keys()),
                    key="simple_numbering_category",
                )
                selected_numbering_category = numbering_category_map[
                    selected_category_label
                ]
                selected_numbering_category_id = int(
                    row_value(selected_numbering_category, "id", 0)
                )
                category_limit = get_pallet_category_numbering_limit(
                    conn,
                    selected_numbering_category_id,
                )
                current_next_sequence = int(
                    category_limit["current_next_sequence"]
                )
                minimum_next_sequence = int(
                    category_limit["minimum_next_sequence"]
                )

                next_info_col1, next_info_col2 = st.columns(2)

                with next_info_col1:
                    st.metric(
                        "現在の次回番号",
                        f"{current_next_sequence:03d}",
                    )

                with next_info_col2:
                    st.metric(
                        "設定できる最小番号",
                        f"{minimum_next_sequence:03d}",
                    )

                with st.form(
                    "simple_numbering_next_form_"
                    f"{selected_numbering_category_id}"
                ):
                    new_next_sequence = st.number_input(
                        "新しい次回開始番号",
                        min_value=minimum_next_sequence,
                        max_value=999_999,
                        value=max(
                            current_next_sequence,
                            minimum_next_sequence,
                        ),
                        step=1,
                    )
                    next_sequence_confirmed = st.checkbox(
                        "次回の登録からこの番号を使う"
                    )
                    next_sequence_submitted = st.form_submit_button(
                        "次回開始番号を変更",
                        type="primary",
                        use_container_width=True,
                    )

                if next_sequence_submitted:
                    if not next_sequence_confirmed:
                        st.warning("確認にチェックを入れてください。")
                    else:
                        try:
                            next_result = (
                                set_pallet_category_next_sequence(
                                    conn=conn,
                                    category_id=(
                                        selected_numbering_category_id
                                    ),
                                    next_sequence=int(new_next_sequence),
                                )
                            )
                            st.session_state.simple_admin_flash = (
                                f"{next_result['category_name']} の"
                                "次回開始番号を "
                                f"{next_result['next_sequence']:03d} "
                                "に変更しました。"
                            )
                            st.rerun()

                        except PalletError as exc:
                            st.error(str(exc))

                        except Exception as exc:
                            st.error(
                                "次回開始番号の変更中にエラーが"
                                f"発生しました：{exc}"
                            )

        if st.session_state.simple_admin_action == "入庫待ち":
            st.subheader("🖨️ 入庫待ち")
            st.caption(
                "入庫待ちA4の再印刷または登録取消を行います。"
            )
            st.divider()
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

        if st.session_state.simple_admin_action == "登録内容修正":
            st.subheader("✏️ 登録内容修正")
            st.caption(
                "入庫済み・未出庫データの修正や誤登録削除を行います。"
            )
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
