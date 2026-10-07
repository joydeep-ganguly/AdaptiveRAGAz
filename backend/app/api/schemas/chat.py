from pydantic import BaseModel, Field


class ChatQueryRequest(BaseModel):
    message: str


class ChatQueryResponse(BaseModel):
    conversation_id: str
    role: str
    content: str
    media_filenames: list[str] = Field(default_factory=list)