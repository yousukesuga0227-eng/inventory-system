"""SHARK 商品コード自動採番・QRペイロード共通処理。

商品コード形式:
    荷主コード-大カテゴリーコード-YYYYMM-4桁連番

例:
    NTR-KAG-202608-0001
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, unquote

JST = timezone(timedelta(hours=9), name="JST")
QR_PREFIX = "SHARK1"
MAX_SEQUENCE = 9999
_CODE_PART_PATTERN = re.compile(r"[^A-Z0-9]")


class ItemCodeError(Exception):
    """商品コード採番処理の基本例外。"""


class ItemCodeMasterError(ItemCodeError):
    """荷主・カテゴリー等のマスタ設定不足。"""


class ItemCodeLimitError(ItemCodeError):
    """月内4桁連番を使い切った。"""


def _connection_candidates(conn):
    candidates = [conn]
    seen = set()

    while candidates:
        candidate = candidates.pop(0)
        candidate_id = id(candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        yield candidate

        for attribute in (
            "conn",
            "_conn",
            "connection",
            "_connection",
            "raw_conn",
            "_raw_conn",
        ):
            try:
                wrapped = getattr(candidate, attribute)
            except (AttributeError, TypeError):
                continue
            if wrapped is not None and wrapped is not candidate:
                candidates.append(wrapped)


def is_postgres(conn) -> bool:
    for candidate in _connection_candidates(conn):
        type_name = (
            f"{type(candidate).__module__}.{type(candidate).__name__}"
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


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return {key: row[key] for key in row.keys()}
    except (AttributeError, TypeError, KeyError):
        pass
    try:
        return dict(row)
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(JST)


def normalize_code_part(value: Any, field_name: str, max_length: int = 12) -> str:
    text = str(value or "").upper().strip()
    text = _CODE_PART_PATTERN.sub("", text)

    if not text:
        raise ItemCodeMasterError(f"{field_name}が未設定です。")
    if len(text) > max_length:
        raise ItemCodeMasterError(
            f"{field_name}は英数字{max_length}文字以内にしてください。"
        )
    return text


def normalize_year_month(value: date | datetime | str | None = None) -> str:
    if value is None:
        return _now().strftime("%Y%m")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(JST)
        return value.strftime("%Y%m")
    if isinstance(value, date):
        return value.strftime("%Y%m")

    text = str(value).strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 6:
        year_month = digits[:6]
        try:
            datetime.strptime(year_month, "%Y%m")
        except ValueError as exc:
            raise ItemCodeError("登録年月が不正です。") from exc
        return year_month
    raise ItemCodeError("登録年月はYYYYMM形式で指定してください。")


def ensure_item_code_schema(conn) -> None:
    """採番マスタ・カウンター・items追加列を安全に用意する。"""

    if is_postgres(conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_code_categories (
                id BIGSERIAL PRIMARY KEY,
                company_id BIGINT NOT NULL,
                category_code TEXT NOT NULL,
                category_name TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                UNIQUE(company_id, category_code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_code_counters (
                company_id BIGINT NOT NULL,
                category_id BIGINT NOT NULL,
                year_month VARCHAR(6) NOT NULL,
                last_number INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(company_id, category_id, year_month)
            )
            """
        )
        for column_sql in (
            "ALTER TABLE items ADD COLUMN IF NOT EXISTS major_category_code TEXT",
            "ALTER TABLE items ADD COLUMN IF NOT EXISTS major_category_name TEXT",
            "ALTER TABLE items ADD COLUMN IF NOT EXISTS registered_year_month VARCHAR(6)",
            "ALTER TABLE items ADD COLUMN IF NOT EXISTS qr_payload TEXT",
        ):
            conn.execute(column_sql)
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_code_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                category_code TEXT NOT NULL,
                category_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(company_id, category_code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_code_counters (
                company_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                year_month TEXT NOT NULL,
                last_number INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(company_id, category_id, year_month)
            )
            """
        )

        existing_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(items)").fetchall()
        }
        additions = {
            "major_category_code": "TEXT",
            "major_category_name": "TEXT",
            "registered_year_month": "TEXT",
            "qr_payload": "TEXT",
        }
        for column_name, column_type in additions.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE items ADD COLUMN {column_name} {column_type}"
                )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_item_code_categories_company
        ON item_code_categories(company_id, is_active, category_code)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_items_major_category_code
        ON items(major_category_code)
        """
    )
    conn.commit()


def get_project_company(conn, project_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            p.id AS project_id,
            p.code AS project_code,
            p.name AS project_name,
            c.id AS company_id,
            c.code AS company_code,
            c.name AS company_name
        FROM projects p
        LEFT JOIN project_companies pc
            ON p.id = pc.project_id
        LEFT JOIN companies c
            ON pc.company_id = c.id
        WHERE p.id = ?
        ORDER BY c.id
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    data = _row_to_dict(row)

    if not data:
        raise ItemCodeMasterError("案件が見つかりません。")
    if not data.get("company_id"):
        raise ItemCodeMasterError("案件に荷主が設定されていません。")

    data["company_code"] = normalize_code_part(
        data.get("company_code"), "荷主コード"
    )
    return data


def list_item_categories(
    conn,
    company_id: int,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    query = """
        SELECT
            id,
            company_id,
            category_code,
            category_name,
            is_active,
            created_at,
            updated_at
        FROM item_code_categories
        WHERE company_id = ?
    """
    params: list[Any] = [company_id]
    if not include_inactive:
        query += " AND COALESCE(is_active, TRUE) = TRUE"
    query += " ORDER BY category_code, category_name"

    return [
        _row_to_dict(row) or {}
        for row in conn.execute(query, params).fetchall()
    ]


def get_item_category(conn, company_id: int, category_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, company_id, category_code, category_name, is_active
        FROM item_code_categories
        WHERE company_id = ? AND id = ?
        """,
        (company_id, category_id),
    ).fetchone()
    data = _row_to_dict(row)
    if not data:
        raise ItemCodeMasterError("大カテゴリーが見つかりません。")
    data["category_code"] = normalize_code_part(
        data.get("category_code"), "大カテゴリーコード", max_length=8
    )
    return data


def get_item_category_by_code(
    conn,
    company_id: int,
    category_code: str,
    include_inactive: bool = False,
) -> dict[str, Any] | None:
    normalized = normalize_code_part(
        category_code, "大カテゴリーコード", max_length=8
    )
    query = """
        SELECT id, company_id, category_code, category_name, is_active
        FROM item_code_categories
        WHERE company_id = ? AND category_code = ?
    """
    params: list[Any] = [company_id, normalized]
    if not include_inactive:
        query += " AND COALESCE(is_active, TRUE) = TRUE"
    row = conn.execute(query, params).fetchone()
    return _row_to_dict(row)


def upsert_item_category(
    conn,
    company_id: int,
    category_code: str,
    category_name: str,
    is_active: bool = True,
) -> dict[str, Any]:
    normalized_code = normalize_code_part(
        category_code, "大カテゴリーコード", max_length=8
    )
    normalized_name = str(category_name or "").strip()
    if not normalized_name:
        raise ItemCodeMasterError("大カテゴリー名を入力してください。")

    now = _now()
    conn.execute(
        """
        INSERT INTO item_code_categories (
            company_id,
            category_code,
            category_name,
            is_active,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, category_code)
        DO UPDATE SET
            category_name = excluded.category_name,
            is_active = excluded.is_active,
            updated_at = excluded.updated_at
        """,
        (
            company_id,
            normalized_code,
            normalized_name,
            bool(is_active),
            now,
            now,
        ),
    )
    conn.commit()

    data = get_item_category_by_code(
        conn,
        company_id,
        normalized_code,
        include_inactive=True,
    )
    if not data:
        raise ItemCodeError("大カテゴリーの保存結果を取得できませんでした。")
    return data


def format_item_code(
    company_code: str,
    category_code: str,
    year_month: str,
    sequence: int,
) -> str:
    company = normalize_code_part(company_code, "荷主コード")
    category = normalize_code_part(
        category_code, "大カテゴリーコード", max_length=8
    )
    ym = normalize_year_month(year_month)
    sequence = int(sequence)
    if not 1 <= sequence <= MAX_SEQUENCE:
        raise ItemCodeLimitError(
            f"連番は0001～{MAX_SEQUENCE:04d}の範囲です。"
        )
    return f"{company}-{category}-{ym}-{sequence:04d}"



def _existing_max_sequence(conn, company_code: str, category_code: str, year_month: str) -> int:
    prefix = f"{company_code}-{category_code}-{year_month}-"
    row = conn.execute(
        """
        SELECT code
        FROM items
        WHERE code LIKE ?
        ORDER BY code DESC
        LIMIT 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    data = _row_to_dict(row)
    if not data:
        return 0

    code = str(data.get("code", ""))
    parsed = parse_item_code(code)
    if not parsed:
        return 0
    return int(parsed["sequence"])

def peek_next_item_code(
    conn,
    project_id: int,
    category_id: int,
    registration_date: date | datetime | str | None = None,
) -> str:
    company = get_project_company(conn, project_id)
    category = get_item_category(conn, company["company_id"], category_id)
    year_month = normalize_year_month(registration_date)

    row = conn.execute(
        """
        SELECT last_number
        FROM item_code_counters
        WHERE company_id = ? AND category_id = ? AND year_month = ?
        """,
        (company["company_id"], category_id, year_month),
    ).fetchone()
    data = _row_to_dict(row)
    if data:
        current_number = int(data["last_number"])
    else:
        current_number = _existing_max_sequence(
            conn,
            company["company_code"],
            category["category_code"],
            year_month,
        )
    next_number = current_number + 1
    if next_number > MAX_SEQUENCE:
        raise ItemCodeLimitError(
            f"{year_month}の4桁連番を使い切っています。"
        )
    return format_item_code(
        company["company_code"],
        category["category_code"],
        year_month,
        next_number,
    )


def reserve_next_item_code(
    conn,
    project_id: int,
    category_id: int,
    registration_date: date | datetime | str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any], str, int]:
    """DBで連番を1つ確保し、商品コードと関連情報を返す。"""

    company = get_project_company(conn, project_id)
    category = get_item_category(conn, company["company_id"], category_id)
    year_month = normalize_year_month(registration_date)
    now = _now()
    existing_max = _existing_max_sequence(
        conn,
        company["company_code"],
        category["category_code"],
        year_month,
    )
    initial_number = existing_max + 1

    if is_postgres(conn):
        row = conn.execute(
            """
            INSERT INTO item_code_counters (
                company_id,
                category_id,
                year_month,
                last_number,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(company_id, category_id, year_month)
            DO UPDATE SET
                last_number = item_code_counters.last_number + 1,
                updated_at = excluded.updated_at
            RETURNING last_number
            """,
            (
                company["company_id"],
                category_id,
                year_month,
                initial_number,
                now,
            ),
        ).fetchone()
        data = _row_to_dict(row)
        sequence = int(data["last_number"])
    else:
        conn.execute(
            """
            INSERT OR IGNORE INTO item_code_counters (
                company_id,
                category_id,
                year_month,
                last_number,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                company["company_id"],
                category_id,
                year_month,
                existing_max,
                now.isoformat(),
            ),
        )
        conn.execute(
            """
            UPDATE item_code_counters
            SET last_number = last_number + 1, updated_at = ?
            WHERE company_id = ? AND category_id = ? AND year_month = ?
            """,
            (
                now.isoformat(),
                company["company_id"],
                category_id,
                year_month,
            ),
        )
        row = conn.execute(
            """
            SELECT last_number
            FROM item_code_counters
            WHERE company_id = ? AND category_id = ? AND year_month = ?
            """,
            (company["company_id"], category_id, year_month),
        ).fetchone()
        data = _row_to_dict(row)
        sequence = int(data["last_number"])

    if sequence > MAX_SEQUENCE:
        raise ItemCodeLimitError(
            f"{company['company_code']}・{category['category_code']}・"
            f"{year_month}の4桁連番を使い切っています。"
        )

    item_code = format_item_code(
        company["company_code"],
        category["category_code"],
        year_month,
        sequence,
    )
    return item_code, company, category, year_month, sequence


def parse_item_code(item_code: str) -> dict[str, Any] | None:
    text = str(item_code or "").strip()
    parts = text.rsplit("-", 3)
    if len(parts) != 4:
        return None
    company_code, category_code, year_month, sequence_text = parts
    if not re.fullmatch(r"\d{6}", year_month):
        return None
    if not re.fullmatch(r"\d{4}", sequence_text):
        return None
    return {
        "company_code": company_code,
        "category_code": category_code,
        "year_month": year_month,
        "sequence": int(sequence_text),
    }


def _encode_qr_value(value: Any) -> str:
    return quote(str(value if value is not None else ""), safe="-_.~")


def _decode_qr_value(value: str) -> str:
    return unquote(value)


def build_item_qr_payload(
    *,
    item_code: str,
    company_code: str,
    category_code: str,
    year_month: str,
    sequence: int,
    project_code: str = "",
    project_id: int | str = "",
    item_name: str = "",
    required_quantity: int | str = 1,
    unit_number: int | None = None,
) -> str:
    fields: list[tuple[str, Any]] = [
        ("TYPE", "ITEM"),
        ("ITEM", item_code),
        ("COMPANY", company_code),
        ("CATEGORY", category_code),
        ("YM", year_month),
        ("SEQ", f"{int(sequence):04d}"),
        ("PROJECT", project_code),
        ("PROJECT_ID", project_id),
        ("NAME", item_name),
        ("QTY", required_quantity),
    ]
    if unit_number is not None:
        fields.append(("UNIT", f"{int(unit_number):03d}"))

    return "|".join(
        [QR_PREFIX]
        + [f"{key}={_encode_qr_value(value)}" for key, value in fields]
    )


def parse_shark_qr_payload(value: Any) -> dict[str, str] | None:
    text = str(value or "").strip()
    if not text.startswith(f"{QR_PREFIX}|"):
        return None

    fields: dict[str, str] = {}
    for part in text.split("|")[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        fields[key.strip().upper()] = _decode_qr_value(raw_value)
    return fields


def extract_item_code_from_qr(value: Any) -> str | None:
    fields = parse_shark_qr_payload(value)
    if not fields:
        return None

    item_code = (
        fields.get("ITEM")
        or fields.get("ITEM_CODE")
        or fields.get("CODE")
        or ""
    ).strip()
    if not item_code:
        return None

    unit_text = fields.get("UNIT", "").strip()
    if unit_text.isdigit():
        return f"{item_code}-{int(unit_text):03d}"
    return item_code
