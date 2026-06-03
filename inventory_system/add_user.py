from database import get_connection

conn = get_connection()

conn.execute(
    """
    INSERT INTO users (
        username,
        password,
        role
    )
    VALUES (?, ?, ?)
    """,
    (
        "壽賀洋佑",
        "0227",
        "admin"
    )
)

conn.commit()

print("追加完了")