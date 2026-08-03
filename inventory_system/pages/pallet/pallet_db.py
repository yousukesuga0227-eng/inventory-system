"""
SHARK パレット管理のDB処理。

SQLite と PostgreSQL（Supabase）の両方で動作する。
"""

import os
import re
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4


JST = timezone(timedelta(hours=9), name="JST")
_CONNECTION_ATTRIBUTES = (
    "_conn",
    "conn",
    "_connection",
    "connection",
    "_raw_conn",
    "raw_conn",
    "_raw_connection",
    "raw_connection",
)
_CURSOR_ATTRIBUTES = (
    "_cursor",
    "cursor",
    "_wrapped_cursor",
    "wrapped_cursor",
    "_raw_cursor",
    "raw_cursor",
)


class PalletError(Exception):
    """パレット処理の基本例外。"""


class PalletNotFoundError(PalletError):
    """パレットが見つからない。"""


class PalletStockError(PalletError):
    """在庫数量が不正。"""


class PalletConflictError(PalletError):
    """別端末の更新と競合した。"""


def _connection_candidates(conn):
    """互換ラッパーの内側を含め、接続候補を順番に返す。"""

    candidates = [conn]
    seen = set()

    while candidates:
        candidate = candidates.pop(0)
        candidate_id = id(candidate)

        if candidate_id in seen:
            continue

        seen.add(candidate_id)
        yield candidate

        for attribute in _CONNECTION_ATTRIBUTES:
            try:
                wrapped = getattr(candidate, attribute)
            except (AttributeError, TypeError):
                continue

            if wrapped is not None and wrapped is not candidate:
                candidates.append(wrapped)


def _is_postgres(conn):
    """CompatConnection越しでもSupabase接続を正しく判定する。"""

    database_url = os.environ.get("DATABASE_URL", "").strip().lower()

    if database_url.startswith(("postgres://", "postgresql://")):
        return True

    for candidate in _connection_candidates(conn):
        type_name = (
            f"{type(candidate).__module__}."
            f"{type(candidate).__name__}"
        ).lower()

        if "psycopg" in type_name or "postgres" in type_name:
            return True

        try:
            dsn = str(candidate.info.dsn).lower()
        except (AttributeError, TypeError):
            dsn = ""

        if "postgres" in dsn:
            return True

    return False


def _rollback_safely(conn):
    """
    CompatConnectionがrollbackを公開していなくても、
    元の例外を上書きせずにトランザクションを戻す。
    """

    for candidate in _connection_candidates(conn):
        rollback = getattr(candidate, "rollback", None)

        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass
            return

    try:
        conn.execute("ROLLBACK")
    except Exception:
        pass


def _sql(conn, query):
    if _is_postgres(conn):
        return query.replace("?", "%s")

    return query


def _execute(conn, query, params=()):
    return conn.execute(_sql(conn, query), params)


def _not_deleted_condition(column):
    """BOOLEAN / SQLiteの0・1のどちらでも未削除を判定する。"""

    return f"COALESCE({column}, FALSE) = FALSE"


def _cursor_candidates(cursor):
    """CompatCursorの内側を含め、カーソル候補を順番に返す。"""

    candidates = [cursor]
    seen = set()

    while candidates:
        candidate = candidates.pop(0)
        candidate_id = id(candidate)

        if candidate_id in seen:
            continue

        seen.add(candidate_id)
        yield candidate

        for attribute in _CURSOR_ATTRIBUTES:
            try:
                wrapped = getattr(candidate, attribute)
            except (AttributeError, TypeError):
                continue

            if wrapped is not None and wrapped is not candidate:
                candidates.append(wrapped)


def _cursor_attribute(cursor, attribute, default=None):
    """CompatCursorまたは内側の実カーソルから属性を取得する。"""

    for candidate in _cursor_candidates(cursor):
        try:
            value = getattr(candidate, attribute)
        except (AttributeError, TypeError):
            continue

        if value is not None:
            return value

    return default


def _row_to_dict(row, description=None):
    if row is None:
        return None

    if isinstance(row, dict):
        return dict(row)

    try:
        return dict(row)
    except (TypeError, ValueError):
        pass

    if description:
        columns = [
            column.name if hasattr(column, "name") else column[0]
            for column in description
        ]
        return dict(zip(columns, row))

    raise TypeError("DBの検索結果を辞書へ変換できませんでした。")


def _fetchone_dict(cursor):
    row = cursor.fetchone()
    description = _cursor_attribute(cursor, "description")
    return _row_to_dict(row, description)


def _fetchall_dict(cursor):
    rows = cursor.fetchall()
    description = _cursor_attribute(cursor, "description")
    return [
        _row_to_dict(row, description)
        for row in rows
    ]


def _now():
    return datetime.now(JST)


def _create_batch_code():
    now = _now()
    suffix = uuid4().hex[:4].upper()
    return f"BAT{now:%Y%m%d-%H%M%S}-{suffix}"


def _insert_and_get_id(conn, query, params):
    if _is_postgres(conn):
        cursor = _execute(
            conn,
            f"{query.rstrip()} RETURNING id",
            params,
        )
        row = cursor.fetchone()

        if isinstance(row, dict):
            return int(row["id"])

        try:
            return int(row["id"])
        except (KeyError, IndexError, TypeError):
            return int(row[0])

    cursor = _execute(conn, query, params)
    lastrowid = _cursor_attribute(cursor, "lastrowid")

    if lastrowid is None:
        raise PalletError("登録したパレットIDを取得できませんでした。")

    return int(lastrowid)


def _normalize_category_name(value):
    """大カテゴリー名の前後空白を除去して検証する。"""

    name = str(value or "").strip()

    if not name:
        raise PalletStockError("大カテゴリーを入力してください。")

    if len(name) > 100:
        raise PalletStockError(
            "大カテゴリーは100文字以内で入力してください。"
        )

    return name


def list_pallet_categories(conn, include_hidden=False):
    """大カテゴリーを採番順で取得する。"""

    conditions = []
    params = []

    if not include_hidden:
        conditions.append("COALESCE(is_active, TRUE) = TRUE")

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions
        else ""
    )
    cursor = _execute(
        conn,
        f"""
        SELECT
            id,
            name,
            next_sequence,
            is_active,
            created_by,
            created_at,
            updated_at
        FROM pallet_categories
        {where_clause}
        ORDER BY
            CASE WHEN name = '未分類' THEN 1 ELSE 0 END,
            name,
            id
        """,
        tuple(params),
    )
    return _fetchall_dict(cursor)


def _get_category_by_id(conn, category_id, lock=False):
    suffix = " FOR UPDATE" if lock and _is_postgres(conn) else ""
    cursor = _execute(
        conn,
        f"""
        SELECT id, name, next_sequence, is_active
        FROM pallet_categories
        WHERE id = ?
        LIMIT 1{suffix}
        """,
        (int(category_id),),
    )
    return _fetchone_dict(cursor)


def _get_category_by_name(conn, category_name):
    cursor = _execute(
        conn,
        """
        SELECT id, name, next_sequence, is_active
        FROM pallet_categories
        WHERE name = ?
        LIMIT 1
        """,
        (_normalize_category_name(category_name),),
    )
    return _fetchone_dict(cursor)


def create_pallet_category(conn, name, username, commit=True):
    """大カテゴリーを追加する。同名カテゴリーの重複は作らない。"""

    normalized_name = _normalize_category_name(name)
    existing = _get_category_by_name(conn, normalized_name)

    if existing is not None:
        if not bool(existing.get("is_active", True)):
            raise PalletStockError(
                f"大カテゴリー「{normalized_name}」は非表示です。"
                "管理タブから再表示してください。"
            )
        return int(existing["id"])

    now = _now()

    try:
        category_id = _insert_and_get_id(
            conn,
            """
            INSERT INTO pallet_categories (
                name,
                next_sequence,
                is_active,
                created_by,
                created_at,
                updated_at
            )
            VALUES (?, 1, TRUE, ?, ?, ?)
            """,
            (normalized_name, username, now, now),
        )

        if commit:
            conn.commit()
        return category_id

    except Exception:
        _rollback_safely(conn)
        raise


def set_pallet_category_active(conn, category_id, is_active):
    """大カテゴリーを非表示または再表示にする。"""

    category = _get_category_by_id(conn, category_id)

    if category is None:
        raise PalletNotFoundError("大カテゴリーが見つかりません。")

    if category["name"] == "未分類" and not is_active:
        raise PalletStockError("「未分類」は非表示にできません。")

    try:
        _execute(
            conn,
            """
            UPDATE pallet_categories
            SET is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (bool(is_active), _now(), int(category_id)),
        )
        conn.commit()
        return int(category_id)

    except Exception:
        _rollback_safely(conn)
        raise


def _resolve_category(
    conn,
    category_id=None,
    category_name="",
    username="system",
):
    """IDまたは名称からカテゴリーを確定し、必要なら新規追加する。"""

    if category_id not in (None, ""):
        category = _get_category_by_id(conn, category_id)

        if category is None:
            raise PalletNotFoundError("大カテゴリーが見つかりません。")

        if not bool(category.get("is_active", True)):
            raise PalletStockError(
                f"大カテゴリー「{category['name']}」は非表示です。"
            )

        return category

    normalized_name = str(category_name or "").strip() or "未分類"
    category = _get_category_by_name(conn, normalized_name)

    if category is None:
        new_id = create_pallet_category(
            conn,
            normalized_name,
            username,
            commit=False,
        )
        category = _get_category_by_id(conn, new_id)

    if not bool(category.get("is_active", True)):
        raise PalletStockError(
            f"大カテゴリー「{category['name']}」は非表示です。"
            "管理タブから再表示してください。"
        )

    return category


def _reserve_category_sequences(conn, category_id, count):
    """カテゴリー内の表示番号を連続して予約する。欠番は再利用しない。"""

    count = int(count)

    if count < 1:
        raise PalletStockError("採番するパレット枚数が不正です。")

    category = _get_category_by_id(conn, category_id, lock=True)

    if category is None:
        raise PalletNotFoundError("大カテゴリーが見つかりません。")

    start_sequence = int(category.get("next_sequence") or 1)
    next_sequence = start_sequence + count

    if next_sequence > 1_000_000:
        raise PalletStockError(
            "大カテゴリー内のパレット番号が上限に達しました。"
        )

    cursor = _execute(
        conn,
        """
        UPDATE pallet_categories
        SET next_sequence = ?, updated_at = ?
        WHERE id = ? AND next_sequence = ?
        """,
        (
            next_sequence,
            _now(),
            int(category_id),
            start_sequence,
        ),
    )
    rowcount = _cursor_attribute(cursor, "rowcount")

    if rowcount is not None and rowcount != 1:
        raise PalletConflictError(
            "別の端末でパレット番号が採番されました。"
            "もう一度登録してください。"
        )

    return start_sequence


def get_items_for_company(conn, company_id):
    """
    パレット登録で選択できる商品を取得する。

    現行SHARKのDBでは companies と projects に直接の紐付けがないため、
    company_id は登録する荷主の選択値としてのみ使用し、商品一覧の
    絞り込みには使わない。
    """

    # 呼び出し側との互換性のため引数は残す。
    _ = company_id

    cursor = _execute(
        conn,
        """
        SELECT
            i.id,
            i.code,
            i.name,
            p.id AS project_id,
            p.code AS project_code,
            p.name AS project_name
        FROM items i
        LEFT JOIN projects p
            ON p.id = i.project_id
        WHERE
            COALESCE(i.is_hidden, FALSE) = FALSE
            AND COALESCE(i.is_active, TRUE) = TRUE
            AND (
                COALESCE(p.is_hidden, FALSE) = FALSE
                OR p.id IS NULL
            )
        ORDER BY
            p.name,
            i.name
        """
    )

    return _fetchall_dict(cursor)


def create_pallet_batch(
    conn,
    company_id,
    project_id,
    item_id,
    allocations,
    username,
    location="",
    remarks="",
    pallet_codes=None,
    commit=True,
    item_code="",
    item_name="",
    category_id=None,
    category_name="",
    category_start_sequence=None,
):
    """
    パレット一式を入庫登録する。

    QRへ格納する固定コードは、採番されたIDから
    PAL000000001 形式で生成する。
    """

    if not allocations:
        raise PalletStockError("パレットの数量割り振りがありません。")

    normalized_allocations = []

    for allocation in allocations:
        try:
            quantity = int(allocation["個数"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PalletStockError(
                "パレット数量に不正な値があります。"
            ) from exc

        if quantity < 0:
            raise PalletStockError(
                "パレット数量は0以上で入力してください。"
            )

        normalized_allocations.append(quantity)

    if sum(normalized_allocations) <= 0:
        raise PalletStockError(
            "入庫数量の合計は1個以上にしてください。"
        )

    total_pallets = len(normalized_allocations)
    normalized_pallet_codes = [None] * total_pallets

    if pallet_codes is not None:
        normalized_pallet_codes = [
            str(code or "").strip().upper()
            for code in pallet_codes
        ]

        if len(normalized_pallet_codes) != total_pallets:
            raise PalletStockError(
                "パレット数量とQRコードの件数が一致しません。"
            )

        if any(not code for code in normalized_pallet_codes):
            raise PalletStockError("空のQRコードは登録できません。")

        if len(set(normalized_pallet_codes)) != total_pallets:
            raise PalletStockError(
                "同じQRコードが複数のパレットに指定されています。"
            )

    batch_code = _create_batch_code()
    now = _now()
    normalized_location = str(location or "").strip()
    normalized_remarks = str(remarks or "").strip()
    normalized_item_code = str(item_code or "").strip()
    normalized_item_name = str(item_name or "").strip()

    if item_id in ("", None):
        normalized_item_id = None
    else:
        try:
            normalized_item_id = int(item_id)
        except (TypeError, ValueError) as exc:
            raise PalletStockError(
                "商品情報に不正な値があります。"
            ) from exc

    if normalized_item_id is not None and (
        not normalized_item_code or not normalized_item_name
    ):
        cursor = _execute(
            conn,
            "SELECT code, name FROM items WHERE id = ? LIMIT 1",
            (normalized_item_id,),
        )
        item_row = _fetchone_dict(cursor)

        if item_row is not None:
            if not normalized_item_code:
                normalized_item_code = str(
                    item_row.get("code") or ""
                ).strip()

            if not normalized_item_name:
                normalized_item_name = str(
                    item_row.get("name") or ""
                ).strip()

    if not normalized_item_name:
        raise PalletStockError("商品名を入力してください。")

    try:
        category = _resolve_category(
            conn=conn,
            category_id=category_id,
            category_name=category_name,
            username=username,
        )
        normalized_category_id = int(category["id"])
        normalized_category_name = str(category["name"])

        if category_start_sequence in (None, ""):
            start_sequence = _reserve_category_sequences(
                conn,
                normalized_category_id,
                total_pallets,
            )
        else:
            try:
                start_sequence = int(category_start_sequence)
            except (TypeError, ValueError) as exc:
                raise PalletStockError(
                    "大カテゴリー内のパレット番号が不正です。"
                ) from exc

            if start_sequence < 1:
                raise PalletStockError(
                    "大カテゴリー内のパレット番号が不正です。"
                )

        for sequence, (quantity, planned_pallet_code) in enumerate(
            zip(normalized_allocations, normalized_pallet_codes),
            start=1,
        ):
            temporary_code = f"TEMP-{uuid4().hex.upper()}"
            category_sequence = start_sequence + sequence - 1

            pallet_id = _insert_and_get_id(
                conn,
                """
                INSERT INTO pallet_inventory (
                    pallet_code,
                    batch_code,
                    pallet_sequence,
                    total_pallets,
                    company_id,
                    project_id,
                    item_id,
                    item_code,
                    item_name,
                    category_id,
                    category_name,
                    category_sequence,
                    initial_qty,
                    current_qty,
                    status,
                    location,
                    remarks,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    '保管中', ?, ?, ?, ?, ?
                )
                """,
                (
                    temporary_code,
                    batch_code,
                    sequence,
                    total_pallets,
                    company_id,
                    project_id,
                    normalized_item_id,
                    normalized_item_code,
                    normalized_item_name,
                    normalized_category_id,
                    normalized_category_name,
                    category_sequence,
                    quantity,
                    quantity,
                    normalized_location or None,
                    normalized_remarks or None,
                    username,
                    now,
                    now,
                ),
            )

            pallet_code = (
                planned_pallet_code
                or f"PAL{pallet_id:09d}"
            )

            _execute(
                conn,
                """
                UPDATE pallet_inventory
                SET
                    pallet_code = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (pallet_code, now, pallet_id),
            )

            _execute(
                conn,
                """
                INSERT INTO pallet_history (
                    pallet_id,
                    pallet_code,
                    history_type,
                    qty,
                    before_qty,
                    after_qty,
                    username,
                    remarks,
                    created_at
                )
                VALUES (?, ?, '入庫', ?, 0, ?, ?, ?, ?)
                """,
                (
                    pallet_id,
                    pallet_code,
                    quantity,
                    quantity,
                    username,
                    normalized_remarks or None,
                    now,
                ),
            )

        if commit:
            conn.commit()
        return batch_code

    except Exception:
        _rollback_safely(conn)
        raise


def _normalize_receipt_code(receipt_or_pallet_code):
    """パレット固有QRから元の入庫管理番号を取り出す。"""

    normalized = str(receipt_or_pallet_code or "").strip().upper()
    # 1,000枚目以降は P1000 のように4桁以上になるため、
    # 桁数を3桁へ固定せず、事前登録で発行した全QRを受け付ける。
    match = re.fullmatch(r"(IN-\d{6}-\d+)-P\d+", normalized)
    return match.group(1) if match else normalized


def _normalize_receiving_date(value):
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value or "").strip()

    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise PalletStockError(
            "入庫日は YYYY-MM-DD 形式で入力してください。"
        ) from exc


def _normalize_receiving_plan(plan):
    try:
        company_id = int(plan["company_id"])
        pallet_count = int(plan["pallet_count"])
        qty_per_pallet = int(plan["qty_per_pallet"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PalletStockError(
            "入庫予定の顧客・商品・数量に不正な値があります。"
        ) from exc

    raw_item_id = plan.get("item_id")

    if raw_item_id in ("", None):
        item_id = None
    else:
        try:
            item_id = int(raw_item_id)
        except (TypeError, ValueError) as exc:
            raise PalletStockError(
                "入庫予定の商品情報に不正な値があります。"
            ) from exc

    item_code = str(plan.get("item_code") or "").strip()
    item_name = str(plan.get("item_name") or "").strip()

    if not item_name:
        raise PalletStockError("商品名を入力してください。")

    if pallet_count < 1 or pallet_count > 1000:
        raise PalletStockError(
            "パレット枚数は1～1,000枚で入力してください。"
        )

    if qty_per_pallet < 1 or qty_per_pallet > 1_000_000:
        raise PalletStockError(
            "1パレットあたりの商品個数は"
            "1～1,000,000個で入力してください。"
        )

    project_id = plan.get("project_id")

    if project_id in ("", None):
        project_id = None
    else:
        project_id = int(project_id)

    return {
        "receiving_date": _normalize_receiving_date(
            plan.get("receiving_date")
        ),
        "company_id": company_id,
        "project_id": project_id,
        "item_id": item_id,
        "item_code": item_code,
        "item_name": item_name,
        "category_id": plan.get("category_id"),
        "category_name": str(
            plan.get("category_name") or "未分類"
        ).strip(),
        "pallet_count": pallet_count,
        "qty_per_pallet": qty_per_pallet,
        "total_qty": pallet_count * qty_per_pallet,
        "remarks": str(plan.get("remarks") or "").strip(),
    }


def create_receiving_plans(conn, plans, username):
    """入庫予定を1件以上まとめて登録し、入庫管理番号を返す。"""

    prepared_plans = []

    for source_plan in plans:
        plan = dict(source_plan)

        if plan.get("item_id") not in ("", None) and (
            not str(plan.get("item_code") or "").strip()
            or not str(plan.get("item_name") or "").strip()
        ):
            cursor = _execute(
                conn,
                "SELECT code, name FROM items WHERE id = ? LIMIT 1",
                (plan["item_id"],),
            )
            item_row = _fetchone_dict(cursor)

            if item_row is not None:
                if not str(plan.get("item_code") or "").strip():
                    plan["item_code"] = item_row.get("code") or ""

                if not str(plan.get("item_name") or "").strip():
                    plan["item_name"] = item_row.get("name") or ""

        prepared_plans.append(plan)

    normalized_plans = [
        _normalize_receiving_plan(plan)
        for plan in prepared_plans
    ]

    if not normalized_plans:
        raise PalletStockError("登録する入庫予定がありません。")

    now = _now()
    receipt_codes = []

    try:
        for plan in normalized_plans:
            category = _resolve_category(
                conn=conn,
                category_id=plan["category_id"],
                category_name=plan["category_name"],
                username=username,
            )
            category_id = int(category["id"])
            category_name = str(category["name"])
            category_start_sequence = _reserve_category_sequences(
                conn,
                category_id,
                plan["pallet_count"],
            )
            temporary_code = f"TEMP-{uuid4().hex.upper()}"
            plan_id = _insert_and_get_id(
                conn,
                """
                INSERT INTO pallet_receiving_plans (
                    receipt_code,
                    receiving_date,
                    company_id,
                    project_id,
                    item_id,
                    item_code,
                    item_name,
                    category_id,
                    category_name,
                    category_start_sequence,
                    pallet_count,
                    qty_per_pallet,
                    total_qty,
                    status,
                    remarks,
                    created_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    '入庫待ち', ?, ?, ?, ?
                )
                """,
                (
                    temporary_code,
                    plan["receiving_date"].isoformat(),
                    plan["company_id"],
                    plan["project_id"],
                    plan["item_id"],
                    plan["item_code"],
                    plan["item_name"],
                    category_id,
                    category_name,
                    category_start_sequence,
                    plan["pallet_count"],
                    plan["qty_per_pallet"],
                    plan["total_qty"],
                    plan["remarks"] or None,
                    username,
                    now,
                    now,
                ),
            )
            receipt_code = (
                f"IN-{plan['receiving_date']:%y%m%d}-{plan_id:03d}"
            )
            _execute(
                conn,
                """
                UPDATE pallet_receiving_plans
                SET receipt_code = ?, updated_at = ?
                WHERE id = ?
                """,
                (receipt_code, now, plan_id),
            )
            receipt_codes.append(receipt_code)

        conn.commit()
        return receipt_codes

    except Exception:
        _rollback_safely(conn)
        raise


def create_receiving_plan(
    conn,
    receiving_date,
    company_id,
    project_id,
    item_id,
    pallet_count,
    qty_per_pallet,
    username,
    remarks="",
    item_code="",
    item_name="",
    category_id=None,
    category_name="未分類",
):
    """入庫予定を1件登録する。"""

    codes = create_receiving_plans(
        conn=conn,
        plans=[
            {
                "receiving_date": receiving_date,
                "company_id": company_id,
                "project_id": project_id,
                "item_id": item_id,
                "item_code": item_code,
                "item_name": item_name,
                "category_id": category_id,
                "category_name": category_name,
                "pallet_count": pallet_count,
                "qty_per_pallet": qty_per_pallet,
                "remarks": remarks,
            }
        ],
        username=username,
    )
    return codes[0]


def get_receiving_plan_by_code(conn, receipt_or_pallet_code):
    """入庫管理番号または管理票QRから入庫予定を取得する。"""

    receipt_code = _normalize_receipt_code(receipt_or_pallet_code)

    if not receipt_code:
        return None

    cursor = _execute(
        conn,
        f"""
        SELECT
            rp.id,
            rp.receipt_code,
            rp.receiving_date,
            rp.company_id,
            rp.project_id,
            rp.item_id,
            rp.item_code AS entered_item_code,
            rp.item_name AS entered_item_name,
            rp.category_id,
            rp.category_name,
            rp.category_start_sequence,
            rp.pallet_count,
            rp.qty_per_pallet,
            rp.total_qty,
            rp.status,
            rp.batch_code,
            rp.remarks,
            rp.created_by,
            rp.created_at,
            rp.updated_at,
            rp.confirmed_by,
            rp.confirmed_at,
            c.code AS company_code,
            c.name AS company_name,
            p.code AS project_code,
            p.name AS project_name,
            COALESCE(NULLIF(rp.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(rp.item_name, ''), i.name) AS item_name
        FROM pallet_receiving_plans rp
        LEFT JOIN companies c
            ON c.id = rp.company_id
        LEFT JOIN projects p
            ON p.id = rp.project_id
        LEFT JOIN items i
            ON i.id = rp.item_id
        WHERE
            rp.receipt_code = ?
            AND {_not_deleted_condition("rp.is_deleted")}
        LIMIT 1
        """,
        (receipt_code,),
    )
    return _fetchone_dict(cursor)


def list_receiving_plans(conn, status="入庫待ち", limit=500):
    """入庫予定を新しい順で取得する。"""

    conditions = [_not_deleted_condition("rp.is_deleted")]
    params = []

    if status and status != "すべて":
        conditions.append("rp.status = ?")
        params.append(status)

    params.append(int(limit))
    where_clause = " AND ".join(conditions)
    cursor = _execute(
        conn,
        f"""
        SELECT
            rp.id,
            rp.receipt_code,
            rp.receiving_date,
            rp.company_id,
            rp.project_id,
            rp.item_id,
            rp.item_code AS entered_item_code,
            rp.item_name AS entered_item_name,
            rp.category_id,
            rp.category_name,
            rp.category_start_sequence,
            rp.pallet_count,
            rp.qty_per_pallet,
            rp.total_qty,
            rp.status,
            rp.batch_code,
            rp.remarks,
            rp.created_by,
            rp.created_at,
            rp.confirmed_by,
            rp.confirmed_at,
            c.code AS company_code,
            c.name AS company_name,
            p.code AS project_code,
            p.name AS project_name,
            COALESCE(NULLIF(rp.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(rp.item_name, ''), i.name) AS item_name
        FROM pallet_receiving_plans rp
        LEFT JOIN companies c
            ON c.id = rp.company_id
        LEFT JOIN projects p
            ON p.id = rp.project_id
        LEFT JOIN items i
            ON i.id = rp.item_id
        WHERE {where_clause}
        ORDER BY
            rp.receiving_date DESC,
            rp.id DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return _fetchall_dict(cursor)


def cancel_receiving_plan(conn, receipt_code):
    """未確定の入庫予定を取消にする。"""

    plan = get_receiving_plan_by_code(conn, receipt_code)

    if plan is None:
        raise PalletNotFoundError(
            f"入庫管理番号「{receipt_code}」が見つかりません。"
        )

    if plan["status"] != "入庫待ち":
        raise PalletStockError(
            "入庫待ち以外の管理票は取り消せません。"
        )

    try:
        cursor = _execute(
            conn,
            f"""
            UPDATE pallet_receiving_plans
            SET status = '取消', updated_at = ?
            WHERE
                id = ?
                AND status = '入庫待ち'
                AND {_not_deleted_condition("is_deleted")}
            """,
            (_now(), int(plan["id"])),
        )
        rowcount = _cursor_attribute(cursor, "rowcount")

        if rowcount is not None and rowcount != 1:
            raise PalletConflictError(
                "別の端末で入庫予定が更新されました。"
            )

        conn.commit()
        return plan["receipt_code"]

    except Exception:
        _rollback_safely(conn)
        raise


def confirm_receiving_plan(conn, receipt_or_pallet_code, username):
    """管理票QRを在庫と入庫履歴へ確定登録する。"""

    plan = get_receiving_plan_by_code(conn, receipt_or_pallet_code)

    if plan is None:
        raise PalletNotFoundError(
            "入庫管理票が見つかりません。QRを確認してください。"
        )

    if plan["status"] == "入庫済み":
        raise PalletStockError(
            f"{plan['receipt_code']} はすでに入庫登録済みです。"
        )

    if plan["status"] != "入庫待ち":
        raise PalletStockError("取り消された管理票は入庫できません。")

    pallet_count = int(plan["pallet_count"])
    quantity = int(plan["qty_per_pallet"])
    allocations = [
        {
            "パレット番号": f"{index:03d}",
            "個数": quantity,
        }
        for index in range(1, pallet_count + 1)
    ]
    pallet_codes = [
        f"{plan['receipt_code']}-P{index:03d}"
        for index in range(1, pallet_count + 1)
    ]
    now = _now()

    try:
        batch_code = create_pallet_batch(
            conn=conn,
            company_id=plan["company_id"],
            project_id=plan["project_id"],
            item_id=plan["item_id"],
            allocations=allocations,
            username=username,
            remarks=plan["remarks"] or "",
            pallet_codes=pallet_codes,
            commit=False,
            item_code=plan["item_code"],
            item_name=plan["item_name"],
            category_id=plan["category_id"],
            category_name=plan["category_name"],
            category_start_sequence=plan[
                "category_start_sequence"
            ],
        )
        cursor = _execute(
            conn,
            f"""
            UPDATE pallet_receiving_plans
            SET
                status = '入庫済み',
                batch_code = ?,
                confirmed_by = ?,
                confirmed_at = ?,
                updated_at = ?
            WHERE
                id = ?
                AND status = '入庫待ち'
                AND {_not_deleted_condition("is_deleted")}
            """,
            (
                batch_code,
                username,
                now,
                now,
                int(plan["id"]),
            ),
        )
        rowcount = _cursor_attribute(cursor, "rowcount")

        if rowcount is not None and rowcount != 1:
            raise PalletConflictError(
                "別の端末で入庫登録されました。"
                "重複登録はしていません。"
            )

        conn.commit()
        return {
            "receipt_code": plan["receipt_code"],
            "batch_code": batch_code,
            "pallet_count": pallet_count,
            "total_qty": int(plan["total_qty"]),
        }

    except Exception:
        _rollback_safely(conn)
        raise


def get_pallet_by_code(conn, pallet_code):
    """QRコードの固定パレットコードから1件取得する。"""

    normalized_code = str(pallet_code or "").strip().upper()

    if not normalized_code:
        return None

    cursor = _execute(
        conn,
        f"""
        SELECT
            pi.id,
            pi.pallet_code,
            pi.batch_code,
            pi.pallet_sequence,
            pi.total_pallets,
            pi.category_id,
            pi.category_name,
            pi.category_sequence,
            pi.company_id,
            pi.project_id,
            pi.item_id,
            pi.initial_qty,
            pi.current_qty,
            pi.status,
            pi.location,
            pi.remarks,
            pi.created_by,
            pi.created_at,
            pi.updated_at,
            c.name AS company_name,
            p.code AS project_code,
            p.name AS project_name,
            COALESCE(NULLIF(pi.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(pi.item_name, ''), i.name) AS item_name
        FROM pallet_inventory pi
        LEFT JOIN companies c
            ON c.id = pi.company_id
        LEFT JOIN projects p
            ON p.id = pi.project_id
        LEFT JOIN items i
            ON i.id = pi.item_id
        WHERE
            pi.pallet_code = ?
            AND {_not_deleted_condition("pi.is_deleted")}
        LIMIT 1
        """,
        (normalized_code,),
    )

    return _fetchone_dict(cursor)


def get_batch_pallets(conn, batch_code):
    """登録バッチに含まれる全パレットを取得する。"""

    cursor = _execute(
        conn,
        f"""
        SELECT
            pi.id,
            pi.pallet_code,
            pi.batch_code,
            pi.pallet_sequence,
            pi.total_pallets,
            pi.category_id,
            pi.category_name,
            pi.category_sequence,
            pi.company_id,
            pi.project_id,
            pi.item_id,
            pi.initial_qty,
            pi.current_qty,
            pi.status,
            pi.location,
            pi.remarks,
            pi.created_by,
            pi.created_at,
            c.name AS company_name,
            p.code AS project_code,
            p.name AS project_name,
            COALESCE(NULLIF(pi.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(pi.item_name, ''), i.name) AS item_name
        FROM pallet_inventory pi
        LEFT JOIN companies c
            ON c.id = pi.company_id
        LEFT JOIN projects p
            ON p.id = pi.project_id
        LEFT JOIN items i
            ON i.id = pi.item_id
        WHERE
            pi.batch_code = ?
            AND {_not_deleted_condition("pi.is_deleted")}
        ORDER BY pi.pallet_sequence
        """,
        (batch_code,),
    )

    return _fetchall_dict(cursor)


def list_editable_pallet_batches(conn):
    """
    まだ一度も出庫されていない登録Noを新しい順で取得する。

    誤登録の変更・削除は登録No単位で行う。1枚でも出庫されている
    登録Noは対象外にし、在庫と履歴の不整合を防ぐ。
    """

    cursor = _execute(
        conn,
        f"""
        SELECT
            pi.batch_code,
            MIN(pi.created_at) AS created_at,
            MIN(pi.company_id) AS company_id,
            MIN(pi.project_id) AS project_id,
            MIN(pi.item_id) AS item_id,
            MIN(pi.category_id) AS category_id,
            MAX(pi.category_name) AS category_name,
            MIN(pi.category_sequence) AS category_start_sequence,
            MAX(c.name) AS company_name,
            MAX(p.name) AS project_name,
            MAX(
                COALESCE(NULLIF(pi.item_code, ''), i.code, '')
            ) AS item_code,
            MAX(COALESCE(NULLIF(pi.item_name, ''), i.name)) AS item_name,
            COUNT(*) AS pallet_count,
            SUM(pi.initial_qty) AS total_qty
        FROM pallet_inventory pi
        LEFT JOIN companies c
            ON c.id = pi.company_id
        LEFT JOIN projects p
            ON p.id = pi.project_id
        LEFT JOIN items i
            ON i.id = pi.item_id
        WHERE {_not_deleted_condition("pi.is_deleted")}
        GROUP BY pi.batch_code
        HAVING
            SUM(
                CASE
                    WHEN
                        pi.current_qty = pi.initial_qty
                        AND pi.status = '保管中'
                    THEN 1
                    ELSE 0
                END
            ) = COUNT(*)
        ORDER BY
            MIN(pi.created_at) DESC,
            pi.batch_code DESC
        """
    )

    return _fetchall_dict(cursor)


def _normalize_allocation_quantities(allocations):
    if not allocations:
        raise PalletStockError("パレットの数量内訳がありません。")

    quantities = []

    for allocation in allocations:
        try:
            if isinstance(allocation, dict):
                quantity = int(allocation["個数"])
            else:
                quantity = int(allocation)
        except (KeyError, TypeError, ValueError) as exc:
            raise PalletStockError(
                "パレット数量に不正な値があります。"
            ) from exc

        if quantity < 0:
            raise PalletStockError(
                "パレット数量は0以上で入力してください。"
            )

        quantities.append(quantity)

    if sum(quantities) <= 0:
        raise PalletStockError(
            "商品の合計数量は1個以上にしてください。"
        )

    return quantities


def _get_editable_batch_rows(conn, batch_code):
    normalized_batch_code = str(batch_code or "").strip().upper()

    if not normalized_batch_code:
        raise PalletNotFoundError("登録Noが指定されていません。")

    rows = get_batch_pallets(conn, normalized_batch_code)

    if not rows:
        raise PalletNotFoundError(
            f"登録No「{normalized_batch_code}」が見つかりません。"
        )

    for row in rows:
        initial_qty = int(row["initial_qty"])
        current_qty = int(row["current_qty"])
        status = str(row["status"] or "")

        if current_qty != initial_qty or status != "保管中":
            raise PalletStockError(
                "この登録Noはすでに出庫処理されています。"
                "登録内容の変更・削除はできません。"
            )

    return normalized_batch_code, rows


def update_pallet_batch(
    conn,
    batch_code,
    company_id,
    project_id,
    item_id,
    allocations,
    item_code="",
    item_name="",
    category_id=None,
):
    """
    未出庫の登録Noについて、荷主・商品・数量内訳を修正する。

    パレットコードと枚数は維持し、既存の入庫履歴も正しい商品数量へ
    更新する。大カテゴリーが「未分類」の登録に限り、登録済みの
    大カテゴリーへ移してカテゴリー内番号を新しく採番できる。
    """

    normalized_batch_code, rows = _get_editable_batch_rows(
        conn,
        batch_code,
    )
    quantities = _normalize_allocation_quantities(allocations)

    if len(quantities) != len(rows):
        raise PalletStockError(
            "パレット枚数は変更できません。"
            "登録時と同じ枚数で数量を入力してください。"
        )

    now = _now()
    normalized_item_code = str(item_code or "").strip()
    normalized_item_name = str(item_name or "").strip()

    if item_id not in ("", None) and (
        not normalized_item_code or not normalized_item_name
    ):
        cursor = _execute(
            conn,
            "SELECT code, name FROM items WHERE id = ? LIMIT 1",
            (item_id,),
        )
        item_row = _fetchone_dict(cursor)

        if item_row is not None:
            if not normalized_item_code:
                normalized_item_code = str(
                    item_row.get("code") or ""
                ).strip()

            if not normalized_item_name:
                normalized_item_name = str(
                    item_row.get("name") or ""
                ).strip()

    if not normalized_item_name:
        raise PalletStockError("商品名を入力してください。")

    try:
        category_changed = False
        target_category_id = None
        target_category_name = ""
        target_category_start_sequence = None

        if category_id not in (None, ""):
            target_category = _resolve_category(
                conn=conn,
                category_id=category_id,
            )
            requested_category_id = int(target_category["id"])
            requested_category_name = str(target_category["name"])
            already_target_category = all(
                row.get("category_id") not in (None, "")
                and int(row["category_id"]) == requested_category_id
                for row in rows
            )

            if not already_target_category:
                has_classified_row = any(
                    str(row.get("category_name") or "未分類").strip()
                    not in ("", "未分類")
                    for row in rows
                )

                if has_classified_row:
                    raise PalletStockError(
                        "大カテゴリー設定済みの登録は、別の大カテゴリーへ"
                        "変更できません。"
                    )

                if requested_category_name == "未分類":
                    raise PalletStockError(
                        "設定する大カテゴリーを選択してください。"
                    )

                target_category_id = requested_category_id
                target_category_name = requested_category_name
                target_category_start_sequence = (
                    _reserve_category_sequences(
                        conn,
                        target_category_id,
                        len(rows),
                    )
                )
                category_changed = True

        for row_index, (row, quantity) in enumerate(
            zip(rows, quantities)
        ):
            pallet_id = int(row["id"])
            old_initial_qty = int(row["initial_qty"])
            old_current_qty = int(row["current_qty"])
            old_category_id = row.get("category_id")
            old_category_sequence = row.get("category_sequence")

            if category_changed:
                updated_category_id = target_category_id
                updated_category_name = target_category_name
                updated_category_sequence = (
                    target_category_start_sequence + row_index
                )
            else:
                updated_category_id = old_category_id
                updated_category_name = str(
                    row.get("category_name") or "未分類"
                ).strip()
                updated_category_sequence = old_category_sequence

            cursor = _execute(
                conn,
                f"""
                UPDATE pallet_inventory
                SET
                    company_id = ?,
                    project_id = ?,
                    item_id = ?,
                    item_code = ?,
                    item_name = ?,
                    category_id = ?,
                    category_name = ?,
                    category_sequence = ?,
                    initial_qty = ?,
                    current_qty = ?,
                    updated_at = ?
                WHERE
                    id = ?
                    AND initial_qty = ?
                    AND current_qty = ?
                    AND COALESCE(category_id, -1) = ?
                    AND COALESCE(category_sequence, -1) = ?
                    AND status = '保管中'
                    AND {_not_deleted_condition("is_deleted")}
                """,
                (
                    company_id,
                    project_id,
                    item_id,
                    normalized_item_code,
                    normalized_item_name,
                    updated_category_id,
                    updated_category_name,
                    updated_category_sequence,
                    quantity,
                    quantity,
                    now,
                    pallet_id,
                    old_initial_qty,
                    old_current_qty,
                    (
                        int(old_category_id)
                        if old_category_id not in (None, "")
                        else -1
                    ),
                    (
                        int(old_category_sequence)
                        if old_category_sequence not in (None, "")
                        else -1
                    ),
                ),
            )

            rowcount = _cursor_attribute(cursor, "rowcount")

            if rowcount is not None and rowcount != 1:
                raise PalletConflictError(
                    "別の端末で在庫が更新されました。"
                    "画面を更新して確認してください。"
                )

            cursor = _execute(
                conn,
                """
                UPDATE pallet_history
                SET
                    qty = ?,
                    before_qty = 0,
                    after_qty = ?
                WHERE
                    pallet_id = ?
                    AND history_type = '入庫'
                """,
                (
                    quantity,
                    quantity,
                    pallet_id,
                ),
            )

            history_rowcount = _cursor_attribute(cursor, "rowcount")

            if (
                history_rowcount is not None
                and history_rowcount != 1
            ):
                raise PalletConflictError(
                    "入庫履歴の状態が想定と異なります。"
                    "変更を中止しました。"
                )

        conn.commit()

        return {
            "batch_code": normalized_batch_code,
            "pallet_count": len(rows),
            "total_qty": sum(quantities),
            "category_changed": category_changed,
            "category_name": (
                target_category_name
                if category_changed
                else str(rows[0].get("category_name") or "未分類")
            ),
            "category_start_sequence": (
                target_category_start_sequence
                if category_changed
                else rows[0].get("category_sequence")
            ),
        }

    except Exception:
        _rollback_safely(conn)
        raise


def delete_pallet_batch(conn, batch_code):
    """
    未出庫の誤登録を登録No単位で論理削除する。

    在庫一覧と入出庫履歴からは表示しないが、DB上の行は保持する。
    """

    normalized_batch_code, rows = _get_editable_batch_rows(
        conn,
        batch_code,
    )
    now = _now()

    try:
        for row in rows:
            pallet_id = int(row["id"])
            initial_qty = int(row["initial_qty"])
            current_qty = int(row["current_qty"])

            cursor = _execute(
                conn,
                f"""
                UPDATE pallet_inventory
                SET
                    is_deleted = TRUE,
                    updated_at = ?
                WHERE
                    id = ?
                    AND initial_qty = ?
                    AND current_qty = ?
                    AND status = '保管中'
                    AND {_not_deleted_condition("is_deleted")}
                """,
                (
                    now,
                    pallet_id,
                    initial_qty,
                    current_qty,
                ),
            )

            rowcount = _cursor_attribute(cursor, "rowcount")

            if rowcount is not None and rowcount != 1:
                raise PalletConflictError(
                    "別の端末で在庫が更新されました。"
                    "削除を中止しました。"
                )

        conn.commit()

        return {
            "batch_code": normalized_batch_code,
            "pallet_count": len(rows),
            "total_qty": sum(
                int(row["initial_qty"])
                for row in rows
            ),
        }

    except Exception:
        _rollback_safely(conn)
        raise


def ship_pallet(
    conn,
    pallet_code,
    quantity,
    username,
    remarks="",
):
    """指定パレットから数量を出庫し、履歴を残す。"""

    quantity = int(quantity)

    if quantity <= 0:
        raise PalletStockError("出庫数量は1個以上で入力してください。")

    pallet = get_pallet_by_code(conn, pallet_code)

    if pallet is None:
        raise PalletNotFoundError(
            f"パレット「{pallet_code}」が見つかりません。"
        )

    pallet_id = int(pallet["id"])
    before_qty = int(pallet["current_qty"])

    if before_qty <= 0:
        raise PalletStockError("このパレットはすでに出庫済みです。")

    if quantity > before_qty:
        raise PalletStockError(
            f"現在庫は {before_qty:,} 個です。"
            "在庫を超える出庫はできません。"
        )

    after_qty = before_qty - quantity
    new_status = "出庫済み" if after_qty == 0 else "保管中"
    now = _now()

    try:
        cursor = _execute(
            conn,
            f"""
            UPDATE pallet_inventory
            SET
                current_qty = ?,
                status = ?,
                updated_at = ?
            WHERE
                id = ?
                AND current_qty = ?
                AND {_not_deleted_condition("is_deleted")}
            """,
            (
                after_qty,
                new_status,
                now,
                pallet_id,
                before_qty,
            ),
        )

        rowcount = _cursor_attribute(cursor, "rowcount")

        if rowcount is not None and rowcount != 1:
            raise PalletConflictError(
                "別の端末で在庫が更新されました。"
                "QRを読み直してください。"
            )

        _execute(
            conn,
            """
            INSERT INTO pallet_history (
                pallet_id,
                pallet_code,
                history_type,
                qty,
                before_qty,
                after_qty,
                username,
                remarks,
                created_at
            )
            VALUES (?, ?, '出庫', ?, ?, ?, ?, ?, ?)
            """,
            (
                pallet_id,
                pallet["pallet_code"],
                quantity,
                before_qty,
                after_qty,
                username,
                remarks.strip() or None,
                now,
            ),
        )

        conn.commit()

        return {
            "pallet_code": pallet["pallet_code"],
            "shipped_qty": quantity,
            "before_qty": before_qty,
            "after_qty": after_qty,
            "status": new_status,
        }

    except Exception:
        _rollback_safely(conn)
        raise


def list_pallet_stock(
    conn,
    status="保管中",
    search_text="",
):
    """パレット在庫一覧を取得する。"""

    conditions = [
        _not_deleted_condition("pi.is_deleted"),
    ]
    params = []

    if status and status != "すべて":
        conditions.append("pi.status = ?")
        params.append(status)

    search_text = str(search_text or "").strip()

    if search_text:
        conditions.append(
            """
            (
                LOWER(pi.pallet_code) LIKE LOWER(?)
                OR LOWER(COALESCE(pi.category_name, '')) LIKE LOWER(?)
                OR LOWER(COALESCE(c.name, '')) LIKE LOWER(?)
                OR LOWER(COALESCE(p.name, '')) LIKE LOWER(?)
                OR LOWER(
                    COALESCE(NULLIF(pi.item_code, ''), i.code, '')
                ) LIKE LOWER(?)
                OR LOWER(
                    COALESCE(NULLIF(pi.item_name, ''), i.name, '')
                ) LIKE LOWER(?)
                OR LOWER(COALESCE(pi.location, '')) LIKE LOWER(?)
            )
            """
        )
        keyword = f"%{search_text}%"
        params.extend([keyword] * 7)

    where_clause = " AND ".join(conditions)

    cursor = _execute(
        conn,
        f"""
        SELECT
            pi.id,
            pi.pallet_code,
            pi.batch_code,
            pi.pallet_sequence,
            pi.total_pallets,
            pi.category_id,
            pi.category_name,
            pi.category_sequence,
            pi.initial_qty,
            pi.current_qty,
            pi.status,
            pi.location,
            pi.remarks,
            pi.created_by,
            pi.created_at,
            pi.updated_at,
            c.name AS company_name,
            p.code AS project_code,
            p.name AS project_name,
            COALESCE(NULLIF(pi.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(pi.item_name, ''), i.name) AS item_name
        FROM pallet_inventory pi
        LEFT JOIN companies c
            ON c.id = pi.company_id
        LEFT JOIN projects p
            ON p.id = pi.project_id
        LEFT JOIN items i
            ON i.id = pi.item_id
        WHERE {where_clause}
        ORDER BY
            pi.updated_at DESC,
            pi.id DESC
        """,
        tuple(params),
    )

    return _fetchall_dict(cursor)


def list_pallet_history(
    conn,
    history_type="すべて",
    limit=500,
):
    """入出庫履歴を新しい順で取得する。"""

    conditions = [
        _not_deleted_condition("pi.is_deleted"),
    ]
    params = []

    if history_type and history_type != "すべて":
        conditions.append("ph.history_type = ?")
        params.append(history_type)

    params.append(int(limit))
    where_clause = " AND ".join(conditions)

    cursor = _execute(
        conn,
        f"""
        SELECT
            MIN(ph.id) AS id,
            pi.batch_code,
            CASE
                WHEN ph.history_type = '入庫'
                THEN pi.batch_code
                ELSE MIN(ph.pallet_code)
            END AS pallet_code,
            ph.history_type,
            SUM(ph.qty) AS qty,
            SUM(ph.before_qty) AS before_qty,
            SUM(ph.after_qty) AS after_qty,
            ph.username,
            ph.remarks,
            ph.created_at,
            COUNT(DISTINCT ph.pallet_id) AS affected_pallets,
            MAX(pi.category_name) AS category_name,
            MIN(pi.category_sequence) AS category_start_sequence,
            MAX(pi.category_sequence) AS category_end_sequence,
            c.name AS company_name,
            p.name AS project_name,
            COALESCE(NULLIF(pi.item_code, ''), i.code, '') AS item_code,
            COALESCE(NULLIF(pi.item_name, ''), i.name) AS item_name
        FROM pallet_history ph
        INNER JOIN pallet_inventory pi
            ON pi.id = ph.pallet_id
        LEFT JOIN companies c
            ON c.id = pi.company_id
        LEFT JOIN projects p
            ON p.id = pi.project_id
        LEFT JOIN items i
            ON i.id = pi.item_id
        WHERE {where_clause}
        GROUP BY
            pi.batch_code,
            ph.history_type,
            ph.username,
            ph.remarks,
            ph.created_at,
            c.name,
            p.name,
            i.code,
            i.name,
            pi.item_code,
            pi.item_name
        ORDER BY
            ph.created_at DESC,
            MIN(ph.id) DESC
        LIMIT ?
        """,
        tuple(params),
    )

    return _fetchall_dict(cursor)
