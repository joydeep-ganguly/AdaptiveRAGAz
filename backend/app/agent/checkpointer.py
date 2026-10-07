#### Add short term memory

"""LangGraph short-term memory: SqliteSaver over the checkpoints.db file."""

import sqlite3
from functools import lru_cache

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import get_settings


@lru_cache
def get_checkpointer() -> SqliteSaver:
    settings = get_settings()
    checkpoint_path = settings.resolved_checkpoint_db_path
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    return SqliteSaver(conn)