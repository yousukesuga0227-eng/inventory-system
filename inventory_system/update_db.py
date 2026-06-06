import sqlite3

conn = sqlite3.connect("data/inventory.db")

columns = conn.execute(
    """
    PRAGMA table_info(stock_logs)
    """
).fetchall()

column_names = [
    column[1]
    for column in columns
]

if "username" not in column_names:
    conn.execute(
        """
        ALTER TABLE stock_logs
        ADD COLUMN username TEXT
        """
    )

    conn.commit()

    print("username カラムを追加しました")

else:
    print("username カラムはすでにあります")

conn.close()