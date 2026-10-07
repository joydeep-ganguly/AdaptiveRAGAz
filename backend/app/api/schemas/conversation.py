from datetime import datetime

from pydantic import BaseModel


class ConversationCreateRequest(BaseModel):
    title: str = "New conversation"


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}