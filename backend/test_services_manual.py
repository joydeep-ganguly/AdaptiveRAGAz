"""
Manual sanity check for the services layer (point 7). Run from inside backend/:

    python test_services_manual.py
"""

from app.db.session import SessionLocal
from app.db import crud
from app.services import auth_service, chat_service
from app.agent.graph import create_adaptive_rag

db = SessionLocal()

# --- auth_service -------------------------------------------------------
username = "demo_user"
try:
    user = auth_service.register_user(db, username=username, email="demo@example.com", password="testpass123")
    print(f"Registered user: {user.username}")
except auth_service.UserAlreadyExistsError:
    user = crud.get_user_by_username(db, username)
    print(f"User already existed: {user.username}")

token = auth_service.login_user(db, username=username, password="testpass123")
print(f"Access token: {token[:30]}...")

# --- chat_service ---------------------------------------------------------
conversation = crud.create_conversation(db, user_id=user.id, title="Sanity check")
print(f"Created conversation: {conversation.id}")

graph = create_adaptive_rag()

reply = chat_service.send_message(
    db, graph=graph, conversation_id=conversation.id, user_id=user.id,
    user_message="What is Amazon's revenue in 2023?"
)
print(f"Assistant reply: {reply.content[:200]}...")

db.close()