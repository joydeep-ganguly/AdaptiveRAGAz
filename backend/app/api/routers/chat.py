from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas.chat import ChatQueryRequest, ChatQueryResponse
from app.db.models import User
from app.deps import get_agent_graph, get_current_user, get_db
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])


# Deliberately `def`, not `async def` — see section 4 of the plan (bug #4).
# chat_service.send_message calls the synchronous graph.invoke(); a sync def
# lets FastAPI run this in its thread pool instead of blocking the event loop.
@router.post("/{conversation_id}/query", response_model=ChatQueryResponse)
def query_agent(
    conversation_id: str,
    payload: ChatQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    graph=Depends(get_agent_graph),
):
    try:
        assistant_message, media_filenames = chat_service.send_message(
            db, graph=graph, conversation_id=conversation_id, user_id=current_user.id,
            user_message=payload.message,
        )
    except chat_service.ConversationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    return ChatQueryResponse(
        conversation_id=conversation_id,
        role=assistant_message.role,
        content=assistant_message.content,
        media_filenames=media_filenames,
    )