from database import get_connection

conn = get_connection()

rows = conn.execute(
    "PRAGMA table_info(projects)"
).fetchall()

for row in rows:
    print(dict(row))

conn.close()