import streamlit as st

import api_client


def render_sidebar():
    with st.sidebar:
        st.write(f"👤 **{st.session_state.get('username')}**")
        if st.button("Log out"):
            st.session_state.access_token = None
            st.session_state.username = None
            st.session_state.active_conversation_id = None
            st.session_state.draft_conversation = False
            st.rerun()

        st.divider()

        if st.button("➕ New conversation", use_container_width=True):
            # The conversation is not created yet: chat_window.py creates it when the first
            # message is sent, so it can be titled from that message.
            st.session_state.active_conversation_id = None
            st.session_state.draft_conversation = True
            st.rerun()

        st.divider()
        st.caption("Your conversations")

        try:
            conversations = api_client.list_conversations(st.session_state.access_token)
        except api_client.ApiError as exc:
            st.error(f"Could not load conversations: {exc.detail}")
            conversations = []

        for conversation in conversations:
            col1, col2 = st.columns([4, 1])
            is_active = conversation["id"] == st.session_state.get("active_conversation_id")
            label = f"{'📍 ' if is_active else ''}{conversation['title']}"
            if col1.button(label, key=f"conv_{conversation['id']}", use_container_width=True):
                st.session_state.active_conversation_id = conversation["id"]
                st.session_state.draft_conversation = False
                st.rerun()
            if col2.button("🗑️", key=f"del_{conversation['id']}"):
                api_client.delete_conversation(st.session_state.access_token, conversation["id"])
                if is_active:
                    st.session_state.active_conversation_id = None
                st.rerun()