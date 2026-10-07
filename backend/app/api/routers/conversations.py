from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas.conversation import ConversationCreateRequest, ConversationResponse, MessageResponse
from app.db import crud
from app.db.models import User
from app.deps import get_current_user, get_db
from app.services.chat_service import ConversationNotFoundError, get_owned_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return crud.create_conversation(db, user_id=current_user.id, title=payload.title)


@router.get("", response_model=list[ConversationResponse])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return crud.list_conversations_for_user(db, user_id=current_user.id)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def get_conversation_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        get_owned_conversation(db, conversation_id, current_user.id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return crud.list_messages(db, conversation_id)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        conversation = get_owned_conversation(db, conversation_id, current_user.id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    crud.delete_conversation(db, conversation)