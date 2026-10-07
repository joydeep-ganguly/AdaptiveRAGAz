import streamlit as st

from components.auth_forms import render_login_form, render_register_form

st.set_page_config(page_title="Login - Adaptive RAG", page_icon="🔐")

st.title("🔐 Log in")

if st.session_state.get("access_token"):
    st.success(f"Already logged in as **{st.session_state.get('username')}**")
    st.page_link("pages/2_Chat.py", label="Go to Chat", icon="💬")
else:
    tab_login, tab_register = st.tabs(["Log in", "Register"])
    with tab_login:
        render_login_form()
    with tab_register:
        render_register_form()