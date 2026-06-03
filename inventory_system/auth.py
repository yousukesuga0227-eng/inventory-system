import streamlit as st

def check_login():

    if "login" not in st.session_state:
        st.session_state.login = False

    if not st.session_state.login:

        st.error("ログインしてください")

        st.switch_page("app.py")

        st.stop()


def check_admin():

    check_login()

    if st.session_state.role != "admin":

        st.error(
            "管理者権限が必要です"
        )

        st.stop()