import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_current_user, get_db
from app.db.models import User
from app.services.chat_service import ConversationNotFoundError, get_owned_conversation

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{filename}")
def get_media_file(
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Serves a generated chart PNG, scoped to the conversation that produced it.
    Filenames are written by agent/tools/code_tools.py as
    "{conversation_id}_{timestamp}_chart_{idx}.png", so the conversation_id
    prefix is used to enforce the same ownership check used everywhere else.
    """
    settings = get_settings()

    # Reject anything that isn't a bare filename — blocks path traversal
    # (e.g. "../../.env") since this endpoint takes user input straight to disk.
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    conversation_id = safe_name.split("_", 1)[0]
    try:
        get_owned_conversation(db, conversation_id, current_user.id)
    except ConversationNotFoundError:
        # Same 404 whether the file doesn't exist or belongs to someone else — don't reveal which.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    file_path = os.path.join(str(settings.resolved_media_dir), safe_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    return FileResponse(file_path, media_type="image/png")