from fastapi import Request

from app.auth.oauth2 import get_current_active_user, get_current_admin_user, get_current_user
from app.db.session import get_db_session

# Re-exported here so routers only need one import line:
#   from app.deps import get_db, get_current_user, get_current_active_user, get_current_admin_user, get_agent_graph
get_db = get_db_session


def get_agent_graph(request: Request):
    """Returns the compiled LangGraph agent built once at app startup (see main.py's lifespan)."""
    return request.app.state.graph