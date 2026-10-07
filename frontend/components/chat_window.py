import re

import streamlit as st

import api_client

DEFAULT_TITLE = "New conversation"
TITLE_MAX_CHARS = 40


def make_title(prompt: str) -> str:
    """Short conversation title taken from the user's first message."""
    text = " ".join(prompt.split())
    if not text:
        return DEFAULT_TITLE
    if len(text) <= TITLE_MAX_CHARS:
        return text

    cut = text[:TITLE_MAX_CHARS]
    # Prefer ending on a whole word, unless that would leave almost nothing.
    if " " in cut and cut.rfind(" ") >= TITLE_MAX_CHARS // 2:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:!?-") + "…"


_CODE_SPAN = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)


def escape_dollars(text: str) -> str:
    """Stop st.markdown from reading text between two $ signs as a LaTeX formula
    (e.g. "raising $4 billion ... a $14.5 billion valuation"). Code is left untouched,
    because a backslash would show up literally inside code."""
    parts = _CODE_SPAN.split(text)  # odd items are the code spans / blocks
    return "".join(
        part if index % 2 else re.sub(r"(?<!\\)\$", r"\\$", part)
        for index, part in enumerate(parts)
    )


def _render_media(reply: dict):
    for filename in reply.get("media_filenames", []):
        try:
            image_bytes = api_client.get_media_bytes(st.session_state.access_token, filename)
            st.image(image_bytes)
        except api_client.ApiError as exc:
            st.warning(f"Could not load chart '{filename}': {exc.detail}")


def render_chat_window():
    conversation_id = st.session_state.get("active_conversation_id")
    is_draft = st.session_state.get("draft_conversation", False)

    if not conversation_id and not is_draft:
        st.info("Start a new conversation from the sidebar, or select an existing one.")
        return

    # A draft conversation exists only in the UI until its first message is sent.
    messages = []
    if conversation_id:
        try:
            messages = api_client.get_messages(st.session_state.access_token, conversation_id)
        except api_client.ApiError as exc:
            st.error(f"Could not load messages: {exc.detail}")
            return

    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(escape_dollars(message["content"]))

    prompt = st.chat_input("Ask about financial filings, employees, or anything else…")

    if prompt and not conversation_id:
        # First message of a new conversation: create it now, titled from the message, then
        # rerun so the sidebar lists it, and send the message on the next run.
        try:
            conversation = api_client.create_conversation(st.session_state.access_token, title=make_title(prompt))
        except api_client.ApiError as exc:
            st.error(f"Could not start the conversation: {exc.detail}")
            return
        st.session_state.active_conversation_id = conversation["id"]
        st.session_state.draft_conversation = False
        st.session_state.pending_prompt = prompt
        st.rerun()

    if not prompt:
        prompt = st.session_state.pop("pending_prompt", None)

    if prompt:
        with st.chat_message("user"):
            st.markdown(escape_dollars(prompt))

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    reply = api_client.send_message(st.session_state.access_token, conversation_id, prompt)
                    st.markdown(escape_dollars(reply["content"]))
                    _render_media(reply)
                except api_client.ApiError as exc:
                    st.error(f"Something went wrong: {exc.detail}")

        # Deliberately no st.rerun() here — a rerun would refetch history via
        # get_messages(), which carries no media_filenames field, so any chart
        # just rendered above would immediately disappear. Skipping the rerun
        # keeps this turn's chart visible for the rest of this page view.
        # Navigating away and back will still lose it, since chart association
        # isn't persisted — see the note below.