import streamlit as st
import pandas as pd
from database import get_connection
from auth import check_admin

check_admin()

st.title("🏢 企業管理")

conn = get_connection()


# =====================
# 企業登録 いったん非表示
# =====================
st.subheader("企業登録")

company_code = st.text_input("企業コード").upper().strip()
company_name = st.text_input("企業名").strip()

if st.button("企業登録"):
    if not company_code or not company_name:
        st.error("企業コードと企業名を入力してください。")
    else:
        conn.execute("""
            INSERT INTO companies (code, name)
            VALUES (%s, %s)
            ON CONFLICT (code)
            DO UPDATE SET name = EXCLUDED.name
        """, (company_code, company_name))

        conn.commit()
        st.success("企業を登録しました。")
        st.rerun()

# =====================
# CSV一括登録
# =====================
st.subheader("CSV一括登録")

uploaded_file = st.file_uploader(
    "CSVファイルを選択してください",
    type=["csv"]
)

st.caption("CSV形式：企業コード,企業名")

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)

    st.dataframe(df)

    if st.button("CSV登録"):
        for _, row in df.iterrows():
            code = str(row["企業コード"]).upper().strip()
            name = str(row["企業名"]).strip()

            if code and name:
                conn.execute("""
                    INSERT INTO companies (code, name)
                    VALUES (%s, %s)
                    ON CONFLICT (code)
                    DO UPDATE SET name = EXCLUDED.name
                """, (code, name))

        conn.commit()
        st.success("CSV登録が完了しました。")
        st.rerun()

# =====================
# 企業一覧
# =====================
st.subheader("企業一覧")

companies = conn.execute("""
    SELECT
        id,
        code AS 企業コード,
        name AS 企業名,
        is_active AS 有効,
        created_at AS 登録日
    FROM companies
    ORDER BY code
""").fetchall()

companies = [dict(row) for row in companies]

if companies:
    st.dataframe(companies, width="stretch")
else:
    st.info("企業はまだ登録されていません。")

conn.close()