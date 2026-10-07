import streamlit as st

import api_client

st.set_page_config(page_title="Admin - Adaptive RAG", page_icon="🛠️", layout="wide")

if not st.session_state.get("access_token"):
    st.warning("Please log in first.")
    st.page_link("pages/1_Login.py", label="Go to Login", icon="🔐")
    st.stop()

if st.session_state.get("user_type") != "admin":
    st.error("This page is only available to admin users.")
    st.stop()

st.title("🛠️ Admin")

token = st.session_state.access_token

tab_users, tab_code_data, tab_rag_data = st.tabs(["Create user", "Upload analysis data", "Upload RAG document"])

with tab_users:
    st.subheader("Create a new user")
    with st.form("admin_create_user_form"):
        username = st.text_input("Username")
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        user_type = st.selectbox("Type", options=["user", "admin"])
        submitted = st.form_submit_button("Create user")

    if submitted:
        try:
            api_client.create_user_as_admin(token, username, email, password, user_type)
            st.success(f"Created user '{username}' with type '{user_type}'.")
        except api_client.ApiError as exc:
            st.error(f"Could not create user: {exc.detail}")

with tab_code_data:
    st.subheader("Upload a dataset for the code-execution agent")
    uploaded_file = st.file_uploader("CSV or Excel file", type=["csv", "xlsx"], key="code_data_uploader")
    if uploaded_file is not None and st.button("Upload", key="upload_code_data_button"):
        try:
            result = api_client.upload_code_data_file(token, uploaded_file.getvalue(), uploaded_file.name)
            st.success(result["message"])
        except api_client.ApiError as exc:
            st.error(f"Upload failed: {exc.detail}")

with tab_rag_data:
    st.subheader("Upload a financial filing for RAG ingestion")
    uploaded_pdf = st.file_uploader("PDF file", type=["pdf"], key="rag_data_uploader")

    col1, col2 = st.columns(2)
    with col1:
        company_name = st.text_input("Company name (e.g. amazon)")
        doc_type = st.selectbox("Document type", options=["10-k", "10-q", "8-k", "other"])
    with col2:
        fiscal_year = st.number_input("Fiscal year", min_value=1950, max_value=2050, value=2024, step=1)
        fiscal_quarter = st.selectbox(
            "Fiscal quarter (leave blank for annual reports)", options=["", "q1", "q2", "q3", "q4"]
        )

    if uploaded_pdf is not None and st.button("Upload and ingest", key="upload_rag_data_button"):
        with st.spinner("Uploading and ingesting — this can take a while for a large filing…"):
            try:
                result = api_client.upload_rag_data_file(
                    token, uploaded_pdf.getvalue(), uploaded_pdf.name,
                    company_name=company_name, doc_type=doc_type,
                    fiscal_year=int(fiscal_year), fiscal_quarter=fiscal_quarter or None,
                )
                if result["skipped"]:
                    st.info(f"'{result['filename']}' was already ingested — skipped.")
                else:
                    st.success(f"Ingested '{result['filename']}' — {result['pages_ingested']} pages added.")
            except api_client.ApiError as exc:
                st.error(f"Upload/ingestion failed: {exc.detail}")