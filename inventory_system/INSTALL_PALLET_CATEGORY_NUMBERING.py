"""
SHARK パレット大カテゴリー・カテゴリー別連番の安全な追加。

既存の在庫、履歴、QRコード、入庫予定は削除しない。
何度実行しても同じ状態になるように作られている。
"""

from database import get_connection
from pages.pallet.pallet_tables import (
    is_postgres_connection,
    validate_pallet_database,
)


CATEGORY_NAME = "未分類"


def _rollback_safely(conn):
    try:
        conn.rollback()
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass


def _row_value(row, key, index=0, default=None):
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


def _sqlite_table_exists(conn, table_name):
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return int(_row_value(row, "COUNT(*)", 0, 0)) == 1


def _sqlite_columns(conn, table_name):
    return {
        str(_row_value(row, "name", 1, ""))
        for row in conn.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    }


def _sqlite_add_column(conn, table_name, column_name, definition):
    if column_name not in _sqlite_columns(conn, table_name):
        conn.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {definition}"
        )


def _sqlite_default_category_id(conn):
    row = conn.execute(
        "SELECT id FROM pallet_categories WHERE name = ? LIMIT 1",
        (CATEGORY_NAME,),
    ).fetchone()

    if row is None:
        cursor = conn.execute(
            """
            INSERT INTO pallet_categories (
                name, next_sequence, is_active, created_by
            )
            VALUES (?, 1, 1, 'migration')
            """,
            (CATEGORY_NAME,),
        )
        return int(cursor.lastrowid)

    return int(_row_value(row, "id", 0, 0))


def _sqlite_assign_inventory_numbers(conn, default_category_id):
    conn.execute(
        """
        UPDATE pallet_inventory
        SET
            category_id = COALESCE(category_id, ?),
            category_name = COALESCE(NULLIF(TRIM(category_name), ''), ?)
        """,
        (default_category_id, CATEGORY_NAME),
    )

    categories = conn.execute(
        "SELECT id, name, next_sequence FROM pallet_categories"
    ).fetchall()

    for category in categories:
        category_id = int(_row_value(category, "id", 0, 0))
        category_name = str(_row_value(category, "name", 1, ""))
        maximum_row = conn.execute(
            """
            SELECT COALESCE(MAX(category_sequence), 0)
            FROM pallet_inventory
            WHERE category_id = ?
            """,
            (category_id,),
        ).fetchone()
        next_sequence = max(
            int(_row_value(maximum_row, "max", 0, 0)) + 1,
            int(_row_value(category, "next_sequence", 2, 1) or 1),
        )
        rows = conn.execute(
            """
            SELECT id
            FROM pallet_inventory
            WHERE category_id = ? AND category_sequence IS NULL
            ORDER BY id
            """,
            (category_id,),
        ).fetchall()

        for row in rows:
            row_id = int(_row_value(row, "id", 0, 0))
            conn.execute(
                """
                UPDATE pallet_inventory
                SET category_name = ?, category_sequence = ?
                WHERE id = ?
                """,
                (category_name, next_sequence, row_id),
            )
            next_sequence += 1

        conn.execute(
            """
            UPDATE pallet_categories
            SET next_sequence = MAX(next_sequence, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_sequence, category_id),
        )


def _sqlite_assign_plan_numbers(conn, default_category_id):
    conn.execute(
        """
        UPDATE pallet_receiving_plans
        SET
            category_id = COALESCE(category_id, ?),
            category_name = COALESCE(NULLIF(TRIM(category_name), ''), ?)
        """,
        (default_category_id, CATEGORY_NAME),
    )

    plans = conn.execute(
        """
        SELECT
            id, category_id, category_name, pallet_count,
            status, batch_code, category_start_sequence
        FROM pallet_receiving_plans
        ORDER BY id
        """
    ).fetchall()

    for plan in plans:
        plan_id = int(_row_value(plan, "id", 0, 0))
        category_id = int(_row_value(plan, "category_id", 1, 0))
        pallet_count = int(_row_value(plan, "pallet_count", 3, 1))
        status = str(_row_value(plan, "status", 4, ""))
        batch_code = _row_value(plan, "batch_code", 5)
        current_start = _row_value(
            plan,
            "category_start_sequence",
            6,
        )

        if current_start not in (None, ""):
            continue

        start_sequence = None

        if status == "入庫済み" and batch_code:
            row = conn.execute(
                """
                SELECT MIN(category_sequence)
                FROM pallet_inventory
                WHERE batch_code = ?
                """,
                (batch_code,),
            ).fetchone()
            start_sequence = _row_value(row, "min", 0)

        if start_sequence in (None, ""):
            category = conn.execute(
                """
                SELECT next_sequence
                FROM pallet_categories
                WHERE id = ?
                """,
                (category_id,),
            ).fetchone()
            start_sequence = int(
                _row_value(category, "next_sequence", 0, 1) or 1
            )
            conn.execute(
                """
                UPDATE pallet_categories
                SET next_sequence = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (start_sequence + pallet_count, category_id),
            )

        conn.execute(
            """
            UPDATE pallet_receiving_plans
            SET category_start_sequence = ?
            WHERE id = ?
            """,
            (int(start_sequence), plan_id),
        )


def _install_sqlite(conn):
    for table_name in (
        "pallet_inventory",
        "pallet_history",
        "pallet_receiving_plans",
    ):
        if not _sqlite_table_exists(conn, table_name):
            raise RuntimeError(
                f"{table_name} がありません。"
                "先に従来のパレットDB導入を完了してください。"
            )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pallet_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            next_sequence INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (next_sequence >= 1)
        )
        """
    )

    for table_name, columns in {
        "pallet_inventory": (
            ("item_code", "TEXT"),
            ("category_id", "INTEGER"),
            ("category_name", "TEXT"),
            ("category_sequence", "INTEGER"),
        ),
        "pallet_receiving_plans": (
            ("item_code", "TEXT"),
            ("category_id", "INTEGER"),
            ("category_name", "TEXT"),
            ("category_start_sequence", "INTEGER"),
        ),
    }.items():
        for column_name, definition in columns:
            _sqlite_add_column(
                conn,
                table_name,
                column_name,
                definition,
            )

    default_category_id = _sqlite_default_category_id(conn)
    _sqlite_assign_inventory_numbers(conn, default_category_id)
    _sqlite_assign_plan_numbers(conn, default_category_id)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_pallet_inventory_category_sequence
        ON pallet_inventory (category_id, category_sequence)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pallet_receiving_category
        ON pallet_receiving_plans (category_id, category_start_sequence)
        """
    )
    conn.commit()


def _postgres_default_category_id(conn):
    conn.execute(
        """
        INSERT INTO pallet_categories (
            name, next_sequence, is_active, created_by
        )
        VALUES ('未分類', 1, TRUE, 'migration')
        ON CONFLICT (name) DO NOTHING
        """
    )
    row = conn.execute(
        "SELECT id FROM pallet_categories WHERE name = '未分類'"
    ).fetchone()
    return int(_row_value(row, "id", 0, 0))


def _install_postgres(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pallet_categories (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            next_sequence INTEGER NOT NULL DEFAULT 1,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (next_sequence >= 1)
        )
        """
    )
    conn.execute(
        "ALTER TABLE pallet_inventory "
        "ADD COLUMN IF NOT EXISTS item_code TEXT"
    )
    conn.execute(
        "ALTER TABLE pallet_inventory "
        "ADD COLUMN IF NOT EXISTS category_id BIGINT"
    )
    conn.execute(
        "ALTER TABLE pallet_inventory "
        "ADD COLUMN IF NOT EXISTS category_name TEXT"
    )
    conn.execute(
        "ALTER TABLE pallet_inventory "
        "ADD COLUMN IF NOT EXISTS category_sequence INTEGER"
    )
    conn.execute(
        "ALTER TABLE pallet_receiving_plans "
        "ADD COLUMN IF NOT EXISTS item_code TEXT"
    )
    conn.execute(
        "ALTER TABLE pallet_receiving_plans "
        "ADD COLUMN IF NOT EXISTS category_id BIGINT"
    )
    conn.execute(
        "ALTER TABLE pallet_receiving_plans "
        "ADD COLUMN IF NOT EXISTS category_name TEXT"
    )
    conn.execute(
        "ALTER TABLE pallet_receiving_plans "
        "ADD COLUMN IF NOT EXISTS category_start_sequence INTEGER"
    )

    default_category_id = _postgres_default_category_id(conn)
    conn.execute(
        """
        UPDATE pallet_inventory
        SET
            category_id = COALESCE(category_id, %s),
            category_name = COALESCE(NULLIF(TRIM(category_name), ''), %s)
        """,
        (default_category_id, CATEGORY_NAME),
    )
    conn.execute(
        """
        WITH maximum AS (
            SELECT COALESCE(MAX(category_sequence), 0) AS value
            FROM pallet_inventory
            WHERE category_id = %s
        ),
        numbered AS (
            SELECT
                id,
                (SELECT value FROM maximum)
                + ROW_NUMBER() OVER (ORDER BY id) AS sequence_value
            FROM pallet_inventory
            WHERE
                category_id = %s
                AND category_sequence IS NULL
        )
        UPDATE pallet_inventory AS target
        SET category_sequence = numbered.sequence_value
        FROM numbered
        WHERE target.id = numbered.id
        """,
        (default_category_id, default_category_id),
    )
    conn.execute(
        """
        UPDATE pallet_receiving_plans
        SET
            category_id = COALESCE(category_id, %s),
            category_name = COALESCE(NULLIF(TRIM(category_name), ''), %s)
        """,
        (default_category_id, CATEGORY_NAME),
    )
    conn.execute(
        """
        UPDATE pallet_receiving_plans AS rp
        SET category_start_sequence = source.start_sequence
        FROM (
            SELECT
                receiving.id,
                MIN(inventory.category_sequence) AS start_sequence
            FROM pallet_receiving_plans receiving
            JOIN pallet_inventory inventory
                ON inventory.batch_code = receiving.batch_code
            WHERE
                receiving.category_start_sequence IS NULL
                AND receiving.status = '入庫済み'
            GROUP BY receiving.id
        ) AS source
        WHERE rp.id = source.id
        """
    )
    conn.execute(
        """
        WITH maximum AS (
            SELECT COALESCE(MAX(category_sequence), 0) AS value
            FROM pallet_inventory
            WHERE category_id = %s
        ),
        numbered AS (
            SELECT
                id,
                (SELECT value FROM maximum) + 1
                + COALESCE(
                    SUM(pallet_count) OVER (
                        ORDER BY id
                        ROWS BETWEEN UNBOUNDED PRECEDING
                            AND 1 PRECEDING
                    ),
                    0
                ) AS start_sequence
            FROM pallet_receiving_plans
            WHERE
                category_id = %s
                AND category_start_sequence IS NULL
        )
        UPDATE pallet_receiving_plans AS target
        SET category_start_sequence = numbered.start_sequence
        FROM numbered
        WHERE target.id = numbered.id
        """,
        (default_category_id, default_category_id),
    )
    conn.execute(
        """
        UPDATE pallet_categories AS category
        SET next_sequence = GREATEST(
            category.next_sequence,
            COALESCE((
                SELECT MAX(category_sequence) + 1
                FROM pallet_inventory
                WHERE category_id = category.id
            ), 1),
            COALESCE((
                SELECT MAX(category_start_sequence + pallet_count)
                FROM pallet_receiving_plans
                WHERE category_id = category.id
            ), 1)
        ),
        updated_at = CURRENT_TIMESTAMP
        """
    )
    conn.execute(
        """
        ALTER TABLE pallet_inventory
            ALTER COLUMN category_id SET NOT NULL,
            ALTER COLUMN category_name SET NOT NULL,
            ALTER COLUMN category_sequence SET NOT NULL
        """
    )
    conn.execute(
        """
        ALTER TABLE pallet_receiving_plans
            ALTER COLUMN category_id SET NOT NULL,
            ALTER COLUMN category_name SET NOT NULL,
            ALTER COLUMN category_start_sequence SET NOT NULL
        """
    )
    conn.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'pallet_inventory_category_fkey'
            ) THEN
                ALTER TABLE pallet_inventory
                ADD CONSTRAINT pallet_inventory_category_fkey
                FOREIGN KEY (category_id)
                REFERENCES pallet_categories(id);
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'pallet_receiving_category_fkey'
            ) THEN
                ALTER TABLE pallet_receiving_plans
                ADD CONSTRAINT pallet_receiving_category_fkey
                FOREIGN KEY (category_id)
                REFERENCES pallet_categories(id);
            END IF;
        END
        $$
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_pallet_inventory_category_sequence
        ON pallet_inventory (category_id, category_sequence)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pallet_receiving_category
        ON pallet_receiving_plans (category_id, category_start_sequence)
        """
    )
    conn.commit()


def install():
    conn = get_connection()

    try:
        if is_postgres_connection(conn):
            print("PostgreSQL / Supabase に追加しています...")
            _install_postgres(conn)
        else:
            print("SQLite に追加しています...")
            _install_sqlite(conn)

        validate_pallet_database(conn)
        print("完了：大カテゴリーとカテゴリー別連番を追加しました。")

    except Exception:
        _rollback_safely(conn)
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    install()
