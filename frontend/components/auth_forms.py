import streamlit as st

import api_client


def render_login_form():
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        try:
            token = api_client.login(username, password)
            user = api_client.get_me(token)
            st.session_state.access_token = token
            st.session_state.username = user["username"]
            st.session_state.user_type = user["type"]
            st.success(f"Welcome back, {user['username']}!")
            st.rerun()
        except api_client.ApiError as exc:
            st.error(f"Login failed: {exc.detail}")


def render_register_form():
    with st.form("register_form"):
        username = st.text_input("Choose a username")
        email = st.text_input("Email")
        password = st.text_input("Choose a password", type="password")
        submitted = st.form_submit_button("Create account")

    if submitted:
        try:
            api_client.register(username, email, password)
            st.success("Account created — you can log in now.")
        except api_client.ApiError as exc:
            st.error(f"Registration failed: {exc.detail}")