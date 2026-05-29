import sqlite3
import os

os.makedirs(
    "inventory_system/data",
    exist_ok=True
)

DB_PATH = (
    "inventory_system/data/inventory.db"
)

conn = sqlite3.connect(DB_PATH)

cur = conn.cursor()

# projects
cur.execute("""
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT
)
""")

# items
cur.execute("""
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT,
    name TEXT,
    project_id INTEGER
)
""")

# stock_logs
cur.execute("""
CREATE TABLE IF NOT EXISTS stock_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    item_id INTEGER,
    qty INTEGER,
    type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()

conn.close()

print("DB作成完了")