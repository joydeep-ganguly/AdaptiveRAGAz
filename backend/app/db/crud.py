from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Conversation, Message, User


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def create_user(db: Session, username: str, email: str, hashed_password: str, user_type: str = "user") -> User:
    """`hashed_password` must already be hashed — hashing happens in app/auth/security.py (point 6)."""
    user = User(username=username, email=email, hashed_password=hashed_password, type=user_type)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()

def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()

def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.get(User, user_id)


def count_users(db: Session) -> int:
    """Used by auth_service (point #3) to detect whether the next registration is the first user ever."""
    return db.query(User).count()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def create_conversation(db: Session, user_id: int, title: str = "New conversation") -> Conversation:
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_conversation(db: Session, conversation_id: str) -> Optional[Conversation]:
    return db.get(Conversation, conversation_id)


def list_conversations_for_user(db: Session, user_id: int) -> list[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )


def delete_conversation(db: Session, conversation: Conversation) -> None:
    db.delete(conversation)
    db.commit()


def touch_conversation(db: Session, conversation: Conversation) -> None:
    """Bumps updated_at — call this whenever a message is added, so conversation lists sort by recency."""
    conversation.updated_at = datetime.now(timezone.utc)
    db.add(conversation)
    db.commit()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def add_message(db: Session, conversation_id: str, role: str, content: str) -> Message:
    message = Message(conversation_id=conversation_id, role=role, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def list_messages(db: Session, conversation_id: str) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )