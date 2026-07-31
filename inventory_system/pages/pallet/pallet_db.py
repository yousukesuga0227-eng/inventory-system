"""
SHARK パレット管理のDB処理。

SQLite と PostgreSQL（Supabase）の両方で動作する。
"""

import os
from datetime import datetime, timedelta, timezone
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

    batch_code = _create_batch_code()
    total_pallets = len(normalized_allocations)
    now = _now()

    try:
        for sequence, quantity in enumerate(
            normalized_allocations,
            start=1,
        ):
            temporary_code = f"TEMP-{uuid4().hex.upper()}"

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
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
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
                    item_id,
                    quantity,
                    quantity,
                    location.strip() or None,
                    remarks.strip() or None,
                    username,
                    now,
                    now,
                ),
            )

            pallet_code = f"PAL{pallet_id:09d}"

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
                    remarks.strip() or None,
                    now,
                ),
            )

        conn.commit()
        return batch_code

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
            i.code AS item_code,
            i.name AS item_name
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
            i.code AS item_code,
            i.name AS item_name
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
                OR LOWER(COALESCE(c.name, '')) LIKE LOWER(?)
                OR LOWER(COALESCE(p.name, '')) LIKE LOWER(?)
                OR LOWER(COALESCE(i.code, '')) LIKE LOWER(?)
                OR LOWER(COALESCE(i.name, '')) LIKE LOWER(?)
                OR LOWER(COALESCE(pi.location, '')) LIKE LOWER(?)
            )
            """
        )
        keyword = f"%{search_text}%"
        params.extend([keyword] * 6)

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
            i.code AS item_code,
            i.name AS item_name
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
            ph.id,
            ph.pallet_code,
            ph.history_type,
            ph.qty,
            ph.before_qty,
            ph.after_qty,
            ph.username,
            ph.remarks,
            ph.created_at,
            pi.pallet_sequence,
            pi.total_pallets,
            c.name AS company_name,
            p.name AS project_name,
            i.code AS item_code,
            i.name AS item_name
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
        ORDER BY
            ph.created_at DESC,
            ph.id DESC
        LIMIT ?
        """,
        tuple(params),
    )

    return _fetchall_dict(cursor)
