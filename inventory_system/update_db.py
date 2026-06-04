from database import get_connection

conn = get_connection()

columns = [
    ("receive_date", "TEXT"),
    ("shipping_date", "TEXT"),
    ("status", "TEXT"),
    ("memo", "TEXT"),
    ("created_at", "TEXT")
]

for column_name, column_type in columns:

    try:

        conn.execute(
            f"""
            ALTER TABLE projects
            ADD COLUMN {column_name}
            {column_type}
            """
        )

        print(
            f"{column_name}追加完了"
        )

    except Exception as e:

        print(
            f"{column_name}は既に存在"
        )

conn.commit()
conn.close()

print("更新完了")