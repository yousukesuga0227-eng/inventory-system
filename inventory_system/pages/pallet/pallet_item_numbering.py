"""パレット在庫専用の商品コード採番・既存データ移行処理。"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone


JST = timezone(timedelta(hours=9), name="JST")
CODE_PATTERN = re.compile(
    r"^(?P<customer>[A-Z0-9]+)-(?P<category>[A-Z0-9]+)-"
    r"(?P<ym>\d{6})-(?P<number>\d{4})$"
)


class PalletItemNumberingError(RuntimeError):
    """商品コード採番のエラー。"""


def _now():
    return datetime.now(JST)


def _is_postgres(conn):
    database_url = os.environ.get("DATABASE_URL", "").strip().lower()

    if database_url.startswith(("postgres://", "postgresql://")):
        return True

    candidates = [conn]
    seen = set()

    while candidates:
        candidate = candidates.pop(0)

        if id(candidate) in seen:
            continue

        seen.add(id(candidate))
        type_name = (
            f"{type(candidate).__module__}.{type(candidate).__name__}"
        ).lower()

        if "psycopg" in type_name or "postgres" in type_name:
            return True

        for attribute in (
            "conn",
            "_conn",
            "connection",
            "_connection",
            "raw_conn",
            "_raw_conn",
        ):
            wrapped = getattr(candidate, attribute, None)

            if wrapped is not None and wrapped is not candidate:
                candidates.append(wrapped)

    return False


def _rollback(conn):
    candidates = [conn]
    seen = set()

    while candidates:
        candidate = candidates.pop(0)

        if id(candidate) in seen:
            continue

        seen.add(id(candidate))
        rollback = getattr(candidate, "rollback", None)

        if callable(rollback):
            try:
                rollback()
            except Exception:
                pass
            return

        for attribute in (
            "conn",
            "_conn",
            "connection",
            "_connection",
            "raw_conn",
            "_raw_conn",
        ):
            wrapped = getattr(candidate, attribute, None)

            if wrapped is not None and wrapped is not candidate:
                candidates.append(wrapped)


def _row_to_dict(row, cursor=None):
    if row is None:
        return None

    if isinstance(row, dict):
        return dict(row)

    try:
        return dict(row)
    except (TypeError, ValueError):
        pass

    description = getattr(cursor, "description", None)

    if description is None:
        wrapped = getattr(cursor, "cursor", None)
        description = getattr(wrapped, "description", None)

    if description:
        columns = [
            column.name if hasattr(column, "name") else column[0]
            for column in description
        ]
        return dict(zip(columns, row))

    raise TypeError("DB検索結果を辞書へ変換できませんでした。")


def _fetchone(cursor):
    return _row_to_dict(cursor.fetchone(), cursor)


def _fetchall(cursor):
    return [_row_to_dict(row, cursor) for row in cursor.fetchall()]


def _normalize_name(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = " ".join(text.strip().split())
    return text.casefold()


def _clean_customer_code(value):
    code = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    if not code:
        raise PalletItemNumberingError(
            "顧客コードが未設定です。企業管理で顧客コードを登録してください。"
        )

    if len(code) > 12:
        raise PalletItemNumberingError(
            "顧客コードは英数字12文字以内にしてください。"
        )

    return code


def _clean_category_code(value):
    code = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

    if not 2 <= len(code) <= 8:
        raise PalletItemNumberingError(
            "大カテゴリーコードは英数字2～8文字で登録してください。"
        )

    return code


def _registered_ym(value):
    if isinstance(value, datetime):
        value = value.date()

    if isinstance(value, date):
        return value.strftime("%Y%m")

    text = str(value or "").strip().replace("Z", "+00:00")

    if not text:
        return _now().strftime("%Y%m")

    try:
        return datetime.fromisoformat(text).strftime("%Y%m")
    except ValueError:
        digits = re.sub(r"\D", "", text)
        return digits[:6] if len(digits) >= 6 else _now().strftime("%Y%m")


def ensure_pallet_item_numbering_schema(conn):
    """採番用の追加テーブルだけを安全に作成する。"""

    if _is_postgres(conn):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS pallet_category_codes (
                category_id BIGINT PRIMARY KEY,
                category_code TEXT NOT NULL UNIQUE,
                updated_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pallet_item_master (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                category_id BIGINT NOT NULL,
                item_code TEXT NOT NULL UNIQUE,
                item_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                registered_ym TEXT NOT NULL,
                created_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, category_id, normalized_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pallet_item_code_counters (
                company_id INTEGER NOT NULL,
                category_id BIGINT NOT NULL,
                registered_ym TEXT NOT NULL,
                last_number INTEGER NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (company_id, category_id, registered_ym)
            )
            """,
        ]
    else:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS pallet_category_codes (
                category_id INTEGER PRIMARY KEY,
                category_code TEXT NOT NULL UNIQUE,
                updated_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pallet_item_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                item_code TEXT NOT NULL UNIQUE,
                item_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                registered_ym TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (company_id, category_id, normalized_name)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS pallet_item_code_counters (
                company_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                registered_ym TEXT NOT NULL,
                last_number INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_id, category_id, registered_ym)
            )
            """,
        ]

    try:
        for statement in statements:
            conn.execute(statement)

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pallet_item_master_lookup
            ON pallet_item_master (
                company_id,
                category_id,
                normalized_name
            )
            """
        )
        conn.commit()
    except Exception:
        _rollback(conn)
        raise


def list_pallet_category_code_settings(conn):
    ensure_pallet_item_numbering_schema(conn)
    cursor = conn.execute(
        """
        SELECT
            pc.id AS category_id,
            pc.name AS category_name,
            COALESCE(pcc.category_code, '') AS category_code,
            COALESCE(pc.is_active, TRUE) AS is_active
        FROM pallet_categories pc
        LEFT JOIN pallet_category_codes pcc
            ON pcc.category_id = pc.id
        ORDER BY
            CASE WHEN pc.name = '未分類' THEN 1 ELSE 0 END,
            pc.name,
            pc.id
        """
    )
    return _fetchall(cursor)


def set_pallet_category_code(
    conn,
    category_id,
    category_code,
    username="system",
    commit=True,
):
    ensure_pallet_item_numbering_schema(conn)
    clean_code = _clean_category_code(category_code)
    now = _now()

    try:
        exists = _fetchone(
            conn.execute(
                """
                SELECT id, name
                FROM pallet_categories
                WHERE id = ?
                LIMIT 1
                """,
                (int(category_id),),
            )
        )

        if exists is None:
            raise PalletItemNumberingError(
                "大カテゴリーが見つかりません。"
            )

        conn.execute(
            """
            INSERT INTO pallet_category_codes (
                category_id,
                category_code,
                updated_by,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (category_id)
            DO UPDATE SET
                category_code = excluded.category_code,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (
                int(category_id),
                clean_code,
                str(username or "system"),
                now,
                now,
            ),
        )

        if commit:
            conn.commit()

        return clean_code
    except Exception as exc:
        _rollback(conn)

        if "unique" in str(exc).lower():
            raise PalletItemNumberingError(
                f"大カテゴリーコード「{clean_code}」は既に使用されています。"
            ) from exc

        raise


def _company_info(conn, company_id):
    return _fetchone(
        conn.execute(
            """
            SELECT id, code, name
            FROM companies
            WHERE id = ?
            LIMIT 1
            """,
            (int(company_id),),
        )
    )


def _category_info(conn, category_id):
    return _fetchone(
        conn.execute(
            """
            SELECT
                pc.id,
                pc.name,
                COALESCE(pcc.category_code, '') AS category_code
            FROM pallet_categories pc
            LEFT JOIN pallet_category_codes pcc
                ON pcc.category_id = pc.id
            WHERE pc.id = ?
            LIMIT 1
            """,
            (int(category_id),),
        )
    )


def _master_by_name(conn, company_id, category_id, item_name):
    return _fetchone(
        conn.execute(
            """
            SELECT *
            FROM pallet_item_master
            WHERE
                company_id = ?
                AND category_id = ?
                AND normalized_name = ?
            LIMIT 1
            """,
            (
                int(company_id),
                int(category_id),
                _normalize_name(item_name),
            ),
        )
    )


def _insert_master(
    conn,
    company_id,
    category_id,
    item_code,
    item_name,
    registered_ym,
    username,
):
    now = _now()
    conn.execute(
        """
        INSERT INTO pallet_item_master (
            company_id,
            category_id,
            item_code,
            item_name,
            normalized_name,
            registered_ym,
            created_by,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (
            int(company_id),
            int(category_id),
            str(item_code).strip().upper(),
            str(item_name).strip(),
            _normalize_name(item_name),
            registered_ym,
            str(username or "system"),
            now,
            now,
        ),
    )
    return _master_by_name(
        conn,
        company_id,
        category_id,
        item_name,
    )


def _allocate_next_number(conn, company_id, category_id, registered_ym):
    now = _now()
    cursor = conn.execute(
        """
        INSERT INTO pallet_item_code_counters (
            company_id,
            category_id,
            registered_ym,
            last_number,
            updated_at
        )
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT (company_id, category_id, registered_ym)
        DO UPDATE SET
            last_number = pallet_item_code_counters.last_number + 1,
            updated_at = excluded.updated_at
        RETURNING last_number
        """,
        (
            int(company_id),
            int(category_id),
            registered_ym,
            now,
        ),
    )
    row = _fetchone(cursor)
    number = int(row["last_number"])

    if number > 9999:
        raise PalletItemNumberingError(
            "この顧客・大カテゴリー・登録年月の連番が9999に達しました。"
        )

    return number


def resolve_pallet_item_code(
    conn,
    company_id,
    category_id,
    item_name,
    registered_at=None,
    provided_code="",
    username="system",
):
    """
    同じ顧客・大カテゴリー・商品名なら、既存の商品コードを再利用する。
    未採番の商品だけ新しい4桁連番を発行する。
    """

    ensure_pallet_item_numbering_schema(conn)
    clean_name = str(item_name or "").strip()

    if not clean_name:
        raise PalletItemNumberingError("商品名を入力してください。")

    existing = _master_by_name(
        conn,
        company_id,
        category_id,
        clean_name,
    )

    if existing is not None:
        return str(existing["item_code"])

    ym = _registered_ym(registered_at)
    supplied = str(provided_code or "").strip().upper()

    if supplied:
        master = _insert_master(
            conn,
            company_id,
            category_id,
            supplied,
            clean_name,
            ym,
            username,
        )

        if master is None:
            raise PalletItemNumberingError(
                f"商品コード「{supplied}」が別の商品で使用されています。"
            )

        return str(master["item_code"])

    company = _company_info(conn, company_id)
    category = _category_info(conn, category_id)

    if company is None:
        raise PalletItemNumberingError("顧客が見つかりません。")

    if category is None:
        raise PalletItemNumberingError(
            "大カテゴリーが見つかりません。"
        )

    customer_code = _clean_customer_code(company.get("code"))
    category_code = _clean_category_code(category.get("category_code"))
    number = _allocate_next_number(
        conn,
        company_id,
        category_id,
        ym,
    )
    generated = (
        f"{customer_code}-{category_code}-{ym}-{number:04d}"
    )
    master = _insert_master(
        conn,
        company_id,
        category_id,
        generated,
        clean_name,
        ym,
        username,
    )

    if master is None:
        raise PalletItemNumberingError(
            "商品コードの登録が競合しました。もう一度登録してください。"
        )

    return str(master["item_code"])


def _source_rows(conn):
    rows = []

    plan_cursor = conn.execute(
        """
        SELECT
            id,
            company_id,
            category_id,
            item_code,
            item_name,
            receiving_date AS registered_at,
            'plan' AS source_type
        FROM pallet_receiving_plans
        WHERE
            COALESCE(is_deleted, FALSE) = FALSE
            AND status <> '取消'
            AND COALESCE(TRIM(item_name), '') <> ''
        """
    )

    for row in _fetchall(plan_cursor):
        rows.append(row)

    inventory_cursor = conn.execute(
        """
        SELECT
            id,
            company_id,
            category_id,
            item_code,
            item_name,
            created_at AS registered_at,
            'inventory' AS source_type
        FROM pallet_inventory
        WHERE
            COALESCE(is_deleted, FALSE) = FALSE
            AND COALESCE(TRIM(item_name), '') <> ''
        """
    )

    for row in _fetchall(inventory_cursor):
        rows.append(row)

    return rows


def _group_existing_products(conn):
    groups = defaultdict(list)

    for row in _source_rows(conn):
        company_id = row.get("company_id")
        category_id = row.get("category_id")
        item_name = str(row.get("item_name") or "").strip()

        if company_id in (None, "") or category_id in (None, ""):
            continue

        key = (
            int(company_id),
            int(category_id),
            _normalize_name(item_name),
        )
        groups[key].append(row)

    return groups


def _parse_max_number(code, customer_code, category_code, ym):
    match = CODE_PATTERN.fullmatch(str(code or "").strip().upper())

    if not match:
        return 0

    if (
        match.group("customer") != customer_code
        or match.group("category") != category_code
        or match.group("ym") != ym
    ):
        return 0

    return int(match.group("number"))


def _preview_internal(conn):
    ensure_pallet_item_numbering_schema(conn)
    groups = _group_existing_products(conn)
    settings = {
        int(row["category_id"]): str(row.get("category_code") or "")
        for row in list_pallet_category_code_settings(conn)
    }
    companies = {
        int(row["id"]): row
        for row in _fetchall(
            conn.execute("SELECT id, code, name FROM companies")
        )
    }
    masters = {
        (
            int(row["company_id"]),
            int(row["category_id"]),
            str(row["normalized_name"]),
        ): row
        for row in _fetchall(
            conn.execute("SELECT * FROM pallet_item_master")
        )
    }
    simulated = defaultdict(int)

    for row in _fetchall(
        conn.execute(
            """
            SELECT company_id, category_id, registered_ym, last_number
            FROM pallet_item_code_counters
            """
        )
    ):
        simulated[
            (
                int(row["company_id"]),
                int(row["category_id"]),
                str(row["registered_ym"]),
            )
        ] = int(row["last_number"])

    prepared = []

    for key, source_rows in groups.items():
        company_id, category_id, normalized_name = key
        company = companies.get(company_id, {})
        category = _category_info(conn, category_id) or {}
        item_name = min(
            (
                str(row.get("item_name") or "").strip()
                for row in source_rows
                if str(row.get("item_name") or "").strip()
            ),
            key=len,
            default="",
        )
        dates = [
            row.get("registered_at")
            for row in source_rows
            if row.get("registered_at") not in (None, "")
        ]
        oldest = min((str(value) for value in dates), default="")
        ym = _registered_ym(oldest)
        codes = sorted(
            {
                str(row.get("item_code") or "").strip().upper()
                for row in source_rows
                if str(row.get("item_code") or "").strip()
            }
        )
        master = masters.get(key)
        target_code = ""
        status = ""
        reason = ""

        if master is not None:
            target_code = str(master["item_code"])
            status = (
                "変更なし"
                if all(
                    str(row.get("item_code") or "").strip().upper()
                    == target_code.upper()
                    for row in source_rows
                )
                else "既存コードを統一"
            )
        elif len(codes) > 1:
            status = "コード競合"
            reason = "同じ商品に複数の商品コードがあります。"
        elif len(codes) == 1:
            target_code = codes[0]
            status = "既存コードを統一"
        else:
            try:
                customer_code = _clean_customer_code(
                    company.get("code")
                )
                category_code = _clean_category_code(
                    settings.get(category_id, "")
                )
                counter_key = (company_id, category_id, ym)

                # 既存マスターに同じ接頭辞があれば連番上限へ反映。
                for master_row in masters.values():
                    if (
                        int(master_row["company_id"]) == company_id
                        and int(master_row["category_id"]) == category_id
                    ):
                        simulated[counter_key] = max(
                            simulated[counter_key],
                            _parse_max_number(
                                master_row["item_code"],
                                customer_code,
                                category_code,
                                ym,
                            ),
                        )

                simulated[counter_key] += 1
                number = simulated[counter_key]

                if number > 9999:
                    raise PalletItemNumberingError(
                        "4桁連番の上限に達しています。"
                    )

                target_code = (
                    f"{customer_code}-{category_code}-{ym}-{number:04d}"
                )
                status = "採番対象"
            except PalletItemNumberingError as exc:
                status = "設定不足"
                reason = str(exc)

        prepared.append(
            {
                "key": key,
                "company_id": company_id,
                "company_code": str(company.get("code") or ""),
                "company_name": str(company.get("name") or ""),
                "category_id": category_id,
                "category_name": str(category.get("name") or ""),
                "item_name": item_name,
                "registered_ym": ym,
                "current_codes": codes,
                "target_code": target_code,
                "status": status,
                "reason": reason,
                "rows": source_rows,
            }
        )

    prepared.sort(
        key=lambda row: (
            row["company_code"],
            row["category_name"],
            row["registered_ym"],
            row["item_name"],
        )
    )
    return prepared


def preview_existing_pallet_item_numbering(conn):
    result = []

    for row in _preview_internal(conn):
        result.append(
            {
                "状態": row["status"],
                "顧客コード": row["company_code"],
                "顧客名": row["company_name"],
                "大カテゴリー": row["category_name"],
                "商品名": row["item_name"],
                "初回登録年月": row["registered_ym"],
                "現在コード": " / ".join(row["current_codes"]),
                "採番後コード": row["target_code"],
                "対象データ数": len(row["rows"]),
                "確認事項": row["reason"],
            }
        )

    return result


def apply_existing_pallet_item_numbering(conn, username="system"):
    prepared = _preview_internal(conn)
    product_count = 0
    updated_rows = 0
    skipped_count = 0
    now = _now()

    try:
        for product in prepared:
            if product["status"] in {"コード競合", "設定不足"}:
                skipped_count += 1
                continue

            target_code = product["target_code"]

            if not target_code:
                skipped_count += 1
                continue

            resolved = resolve_pallet_item_code(
                conn=conn,
                company_id=product["company_id"],
                category_id=product["category_id"],
                item_name=product["item_name"],
                registered_at=product["registered_ym"] + "01",
                provided_code=(
                    target_code
                    if product["current_codes"]
                    else ""
                ),
                username=username,
            )

            for source in product["rows"]:
                current = str(
                    source.get("item_code") or ""
                ).strip().upper()

                if current == resolved.upper():
                    continue

                if current and current != resolved.upper():
                    continue

                table_name = (
                    "pallet_receiving_plans"
                    if source["source_type"] == "plan"
                    else "pallet_inventory"
                )
                conn.execute(
                    f"""
                    UPDATE {table_name}
                    SET item_code = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        resolved,
                        now,
                        int(source["id"]),
                    ),
                )
                updated_rows += 1

            product_count += 1

        conn.commit()
        return {
            "product_count": product_count,
            "updated_rows": updated_rows,
            "skipped_count": skipped_count,
        }
    except Exception:
        _rollback(conn)
        raise
