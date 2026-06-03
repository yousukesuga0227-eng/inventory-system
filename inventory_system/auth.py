import streamlit as st

def check_login():

    if "login" not in st.session_state:
        st.session_state.login = False

    if not st.session_state.login:

        st.error("ログインしてください")

        st.switch_page("app.py")

        st.stop()