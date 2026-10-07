import streamlit as st

st.set_page_config(page_title="Adaptive RAG", page_icon="🧠", layout="wide")

st.title("🧠 Adaptive RAG")
st.write(
    "Ask questions about financial filings, employee data, general knowledge, "
    "or run data analysis — all through one assistant."
)

if st.session_state.get("access_token"):
    st.success(f"Logged in as **{st.session_state.get('username')}**")
    st.page_link("pages/2_Chat.py", label="Go to Chat", icon="💬")
else:
    st.info("Log in to get started.")
    st.page_link("pages/1_Login.py", label="Go to Login", icon="🔐")