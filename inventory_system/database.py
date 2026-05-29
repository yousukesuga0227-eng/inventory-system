import sqlite3
import os

BASE_DIR = os.path.dirname(__file__)

DB_PATH = os.path.join(
    BASE_DIR,
    "data",
    "inventory.db"
)

def get_connection():

    conn = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn