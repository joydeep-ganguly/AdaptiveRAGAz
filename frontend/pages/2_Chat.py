import streamlit as st

from components.chat_window import render_chat_window
from components.sidebar import render_sidebar

st.set_page_config(page_title="Chat - Adaptive RAG", page_icon="💬", layout="wide")

if not st.session_state.get("access_token"):
    st.warning("Please log in first.")
    st.page_link("pages/1_Login.py", label="Go to Login", icon="🔐")
    st.stop()

st.title("💬 Adaptive RAG Chat")

render_sidebar()
render_chat_window()