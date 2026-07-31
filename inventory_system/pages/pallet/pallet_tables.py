"""
SHARK パレット管理テーブルの読み取り専用検証。

アプリ起動時には CREATE / ALTER / DROP を実行しない。
テーブルの作成・再作成は、同梱の REBUILD_PALLET_TABLES.py を
ユーザーが明示的に実行した場合だけ行う。
"""

import os


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

_POSTGRES_EXPECTED_TYPES = {
    ("pallet_inventory", "id"): "bigint",
    ("pallet_inventory", "pallet_code"): "text",
    ("pallet_inventory", "batch_code"): "text",
    ("pallet_inventory", "pallet_sequence"): "integer",
    ("pallet_inventory", "total_pallets"): "integer",
    ("pallet_inventory", "company_id"): "integer",
    ("pallet_inventory", "project_id"): "integer",
    ("pallet_inventory", "item_id"): "integer",
    ("pallet_inventory", "initial_qty"): "integer",
    ("pallet_inventory", "current_qty"): "integer",
    ("pallet_inventory", "status"): "text",
    ("pallet_inventory", "location"): "text",
    ("pallet_inventory", "remarks"): "text",
    ("pallet_inventory", "created_by"): "text",
    ("pallet_inventory", "created_at"): "timestamp with time zone",
    ("pallet_inventory", "updated_at"): "timestamp with time zone",
    ("pallet_inventory", "is_deleted"): "boolean",
    ("pallet_history", "id"): "bigint",
    ("pallet_history", "pallet_id"): "bigint",
    ("pallet_history", "pallet_code"): "text",
    ("pallet_history", "history_type"): "text",
    ("pallet_history", "qty"): "integer",
    ("pallet_history", "before_qty"): "integer",
    ("pallet_history", "after_qty"): "integer",
    ("pallet_history", "username"): "text",
    ("pallet_history", "remarks"): "text",
    ("pallet_history", "created_at"): "timestamp with time zone",
}

_SQLITE_EXPECTED_TYPES = {
    ("pallet_inventory", "id"): "INTEGER",
    ("pallet_inventory", "pallet_code"): "TEXT",
    ("pallet_inventory", "batch_code"): "TEXT",
    ("pallet_inventory", "pallet_sequence"): "INTEGER",
    ("pallet_inventory", "total_pallets"): "INTEGER",
    ("pallet_inventory", "company_id"): "INTEGER",
    ("pallet_inventory", "project_id"): "INTEGER",
    ("pallet_inventory", "item_id"): "INTEGER",
    ("pallet_inventory", "initial_qty"): "INTEGER",
    ("pallet_inventory", "current_qty"): "INTEGER",
    ("pallet_inventory", "status"): "TEXT",
    ("pallet_inventory", "location"): "TEXT",
    ("pallet_inventory", "remarks"): "TEXT",
    ("pallet_inventory", "created_by"): "TEXT",
    ("pallet_inventory", "created_at"): "TEXT",
    ("pallet_inventory", "updated_at"): "TEXT",
    ("pallet_inventory", "is_deleted"): "INTEGER",
    ("pallet_history", "id"): "INTEGER",
    ("pallet_history", "pallet_id"): "INTEGER",
    ("pallet_history", "pallet_code"): "TEXT",
    ("pallet_history", "history_type"): "TEXT",
    ("pallet_history", "qty"): "INTEGER",
    ("pallet_history", "before_qty"): "INTEGER",
    ("pallet_history", "after_qty"): "INTEGER",
    ("pallet_history", "username"): "TEXT",
    ("pallet_history", "remarks"): "TEXT",
    ("pallet_history", "created_at"): "TEXT",
}


class PalletSchemaError(RuntimeError):
    """パレット用DB定義がアプリの期待と一致しない。"""


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


def is_postgres_connection(conn):
    """CompatConnection越しでもPostgreSQL接続を判定する。"""

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


def _row_value(row, key, index=0, default=None):
    """辞書行・sqlite3.Row・タプルのどれからでも値を取得する。"""

    if row is None:
        return default

    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        pass

    try:
        return row[index]
    except (KeyError, IndexError, TypeError):
        return default


def _schema_error(issues):
    details = "\n".join(f"- {issue}" for issue in issues)
    return PalletSchemaError(
        "パレット用DBの定義が正しくありません。\n"
        "同梱の REBUILD_PALLET_TABLES.py を一度だけ実行してください。\n"
        f"{details}"
    )


def _validate_postgres(conn):
    cursor = conn.execute(
        """
        SELECT
            table_name,
            column_name,
            data_type,
            column_default,
            is_identity
        FROM information_schema.columns
        WHERE
            table_schema = CURRENT_SCHEMA()
            AND table_name IN (
                'pallet_inventory',
                'pallet_history'
            )
        ORDER BY table_name, ordinal_position
        """
    )

    columns = {}

    for row in cursor.fetchall():
        key = (
            str(_row_value(row, "table_name", 0, "")),
            str(_row_value(row, "column_name", 1, "")),
        )
        columns[key] = {
            "data_type": str(
                _row_value(row, "data_type", 2, "")
            ).lower(),
            "column_default": _row_value(
                row,
                "column_default",
                3,
            ),
            "is_identity": str(
                _row_value(row, "is_identity", 4, "NO")
            ).upper(),
        }

    issues = []

    for key, expected_type in _POSTGRES_EXPECTED_TYPES.items():
        actual = columns.get(key)
        qualified_name = f"{key[0]}.{key[1]}"

        if actual is None:
            issues.append(f"{qualified_name} がありません")
            continue

        if actual["data_type"] != expected_type:
            issues.append(
                f"{qualified_name} は {actual['data_type']} 型です"
                f"（必要：{expected_type}）"
            )

    for table_name in ("pallet_inventory", "pallet_history"):
        actual = columns.get((table_name, "id"))

        if actual is None:
            continue

        default_value = str(actual["column_default"] or "").lower()
        has_auto_number = (
            actual["is_identity"] == "YES"
            or "nextval" in default_value
        )

        if not has_auto_number:
            issues.append(f"{table_name}.id に自動採番がありません")

    cursor = conn.execute(
        """
        SELECT
            kcu.table_name,
            kcu.column_name,
            ccu.table_name AS referenced_table,
            ccu.column_name AS referenced_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.constraint_schema = kcu.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
            ON tc.constraint_name = ccu.constraint_name
            AND tc.constraint_schema = ccu.constraint_schema
        WHERE
            tc.constraint_schema = CURRENT_SCHEMA()
            AND tc.constraint_type = 'FOREIGN KEY'
            AND kcu.table_name = 'pallet_history'
            AND kcu.column_name = 'pallet_id'
        """
    )

    foreign_key_ok = False

    for row in cursor.fetchall():
        foreign_key_ok = (
            str(_row_value(row, "referenced_table", 2, ""))
            == "pallet_inventory"
            and str(_row_value(row, "referenced_column", 3, ""))
            == "id"
        )

        if foreign_key_ok:
            break

    if not foreign_key_ok:
        issues.append(
            "pallet_history.pallet_id から "
            "pallet_inventory.id への外部キーがありません"
        )

    if issues:
        raise _schema_error(issues)


def _validate_sqlite(conn):
    columns = {}

    for table_name in ("pallet_inventory", "pallet_history"):
        cursor = conn.execute(f"PRAGMA table_info({table_name})")

        for row in cursor.fetchall():
            column_name = str(_row_value(row, "name", 1, ""))
            data_type = str(_row_value(row, "type", 2, "")).upper()
            primary_key = int(_row_value(row, "pk", 5, 0) or 0)
            columns[(table_name, column_name)] = {
                "data_type": data_type,
                "primary_key": primary_key,
            }

    issues = []

    for key, expected_type in _SQLITE_EXPECTED_TYPES.items():
        actual = columns.get(key)
        qualified_name = f"{key[0]}.{key[1]}"

        if actual is None:
            issues.append(f"{qualified_name} がありません")
            continue

        if actual["data_type"] != expected_type:
            issues.append(
                f"{qualified_name} は {actual['data_type']} 型です"
                f"（必要：{expected_type}）"
            )

    for table_name in ("pallet_inventory", "pallet_history"):
        id_column = columns.get((table_name, "id"))

        if id_column and id_column["primary_key"] != 1:
            issues.append(f"{table_name}.id が主キーではありません")

    cursor = conn.execute("PRAGMA foreign_key_list(pallet_history)")
    foreign_key_ok = False

    for row in cursor.fetchall():
        foreign_key_ok = (
            str(_row_value(row, "table", 2, ""))
            == "pallet_inventory"
            and str(_row_value(row, "from", 3, ""))
            == "pallet_id"
            and str(_row_value(row, "to", 4, ""))
            == "id"
        )

        if foreign_key_ok:
            break

    if not foreign_key_ok:
        issues.append(
            "pallet_history.pallet_id から "
            "pallet_inventory.id への外部キーがありません"
        )

    if issues:
        raise _schema_error(issues)


def validate_pallet_database(conn):
    """
    パレット用テーブルを読み取りだけで検証する。

    この関数はDBを変更しない。定義が違う場合は
    PalletSchemaErrorを発生させ、専用再構築スクリプトの実行を促す。
    """

    if is_postgres_connection(conn):
        _validate_postgres(conn)
    else:
        _validate_sqlite(conn)
