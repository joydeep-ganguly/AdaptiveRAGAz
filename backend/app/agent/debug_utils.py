from typing import Optional

from langchain_core.runnables import RunnableConfig

from app.config import get_settings


def _conversation_id_from_config(config: Optional[RunnableConfig]) -> str:
    """Best-effort extraction — falls back to 'unknown' rather than raising,
    since debug logging must never break the main request path."""
    thread_id = (config or {}).get("configurable", {}).get("thread_id")
    return thread_id or "unknown"


def write_debug_log(config: Optional[RunnableConfig], filename: str, content: str) -> None:
    """
    Write a debug artifact namespaced by conversation_id.

    No-op entirely when DEBUG_MODE is false, so production never touches disk
    for this, and concurrent requests from different conversations never
    collide because each gets its own subdirectory.
    """
    settings = get_settings()
    if not settings.debug_mode:
        return

    conversation_id = _conversation_id_from_config(config)
    debug_dir = settings.resolved_debug_logs_dir / conversation_id
    debug_dir.mkdir(parents=True, exist_ok=True)

    with open(debug_dir / filename, "w", encoding="utf-8") as f:
        f.write(content)