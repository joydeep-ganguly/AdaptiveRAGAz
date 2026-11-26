# backend/app/scaling/schemas.py

from pydantic import BaseModel

from app.api.schemas.chat import ChatQueryResponse


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # queued | running | succeeded | failed
    conversation_id: str
    result: ChatQueryResponse | None = None  # filled in once status is "succeeded"
    error: str | None = None  # filled in once status is "failed"
    usage: dict | None = None  # {"tokens": ..., "llm_calls": ...} once the job has finished (scale profile only)