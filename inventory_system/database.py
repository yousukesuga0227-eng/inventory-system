import os
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "inventory.db"


# =====================
# Streamlit secrets / 環境変数からDB URL取得
# =====================
def get_database_url():
    database_url = os.environ.get("DATABASE_URL")

    if database_url:
        return database_url

    try:
        import streamlit as st

        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]

    except Exception:
        pass

    return None


# =====================
# PostgreSQL用 Row 互換クラス
# row["id"] でも row[0] でも取れるようにする
# =====================
class RowLike:

    def __init__(self, columns, values):
        self.columns = columns
        self.values = values
        self.data = dict(
            zip(columns, values)
        )

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.values[key]

        return self.data[key]

    def keys(self):
        return self.data.keys()


class CompatCursor:

    def __init__(self, cursor):
        self.cursor = cursor
        self.lastrowid = None

    def _columns(self):
        if not self.cursor.description:
            return []

        return [
            col.name if hasattr(col, "name") else col[0]
            for col in self.cursor.description
        ]

    def fetchone(self):
        row = self.cursor.fetchone()

        if row is None:
            return None

        return RowLike(
            self._columns(),
            row
        )

    def fetchall(self):
        rows = self.cursor.fetchall()

        return [
            RowLike(
                self._columns(),
                row
            )
            for row in rows
        ]


class CompatConnection:

    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=()):
        # SQLite の ? を PostgreSQL の %s に変換
        pg_query = query.replace("?", "%s")

        cursor = self.conn.cursor()
        cursor.execute(
            pg_query,
            params
        )

        return CompatCursor(cursor)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


# =====================
# DB接続
# =====================
def get_connection():
    database_url = get_database_url()

    # クラウド用：PostgreSQL
    if database_url:
        import psycopg

        print("使用中DB: PostgreSQL / Supabase")

        conn = psycopg.connect(database_url)

        return CompatConnection(conn)

    # ローカル用：SQLite
    print("使用中DB:", DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    return conn


# =====================
# 操作ログ
# =====================
def log_action(
    username,
    action,
    target_type="",
    target_id=None,
    target_name="",
    detail=""
):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO operation_logs (
            username,
            action,
            target_type,
            target_id,
            target_name,
            detail,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            action,
            target_type,
            target_id,
            target_name,
            detail,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    conn.commit()
    conn.close()