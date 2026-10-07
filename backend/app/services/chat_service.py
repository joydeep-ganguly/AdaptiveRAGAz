import os
import re

from langchain_core.messages import HumanMessage, ToolMessage
from sqlalchemy.orm import Session

from app.db import crud
from app.db.models import Conversation, Message


class ConversationNotFoundError(Exception):
    """Raised when a conversation doesn't exist, or doesn't belong to the requesting user.
    Deliberately the same error for both cases, so this can't be used to probe
    which conversation IDs exist for other users."""


_CHART_SAVED_PATTERN = re.compile(r"Chart saved to (.+)")


def get_owned_conversation(db: Session, conversation_id: str, user_id: int) -> Conversation:
    conversation = crud.get_conversation(db, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise ConversationNotFoundError(conversation_id)
    return conversation


def _extract_text(ai_message) -> str:
    """
    Gemini responses sometimes come back as a plain string, and sometimes as a
    list of content blocks (e.g. when a 'thinking' block is present). Handle
    both so persistence never crashes on the response shape.
    """
    content = ai_message.content
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = [
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if text_parts:
            return "\n".join(text_parts)

    return str(content)


def _extract_chart_filenames(messages) -> list[str]:
    """
    Scans this turn's newly-added messages for ToolMessage outputs from
    run_python_code (agent/tools/code_tools.py), which report saved charts as
    "Chart saved to <absolute path>". Reading the tool's own output (rather
    than the agent's final natural-language reply) is what makes this
    reliable — the LLM isn't guaranteed to repeat the exact filename.
    """
    filenames = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        for match in _CHART_SAVED_PATTERN.finditer(content):
            filenames.append(os.path.basename(match.group(1).strip()))
    return filenames


def send_message(
    db: Session, graph, conversation_id: str, user_id: int, user_message: str
) -> tuple[Message, list[str]]:
    """
    Persists the user's message, runs it through the compiled LangGraph agent
    (using conversation_id as the LangGraph thread_id), persists the assistant's
    reply, and returns (assistant_message, media_filenames) — the latter being
    any chart PNGs the code-execution path generated during THIS turn.

    `graph` is passed in rather than built here — deliberately, so it's built
    once at app startup (point 8) and so tests can substitute a fake/mocked
    graph without touching this function.
    """
    conversation = get_owned_conversation(db, conversation_id, user_id)

    crud.add_message(db, conversation.id, role="user", content=user_message)

    config = {"configurable": {"thread_id": conversation.id}}

    # result["messages"] is the FULL accumulated thread history — snapshot
    # how many messages existed before this call so we can isolate what
    # THIS turn actually added.
    previous_state = graph.get_state(config)
    previous_message_count = len(previous_state.values.get("messages", []))

    result = graph.invoke({"messages": [HumanMessage(user_message)]}, config=config)

    new_messages = result["messages"][previous_message_count:]

    assistant_text = _extract_text(result["messages"][-1])
    media_filenames = _extract_chart_filenames(new_messages)

    assistant_message = crud.add_message(db, conversation.id, role="assistant", content=assistant_text)
    crud.touch_conversation(db, conversation)

    return assistant_message, media_filenames