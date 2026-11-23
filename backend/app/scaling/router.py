# backend/app/scaling/router.py

"""
Queue-based chat endpoints. Mounted by main.py only when SCALING_ENABLED=true.

POST /chat/{conversation_id}/jobs  -> 202 + job_id (returns immediately)
GET  /chat/jobs/{job_id}           -> status, and the answer once finished
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.api.schemas.chat import ChatQueryRequest, ChatQueryResponse
from app.config import get_settings
from app.db.models import User
from app.deps import get_current_user, get_db
from app.scaling.jobs import JobStore
from app.scaling.redis_client import get_redis
from app.scaling.schemas import JobStatusResponse, JobSubmitResponse
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["chat-jobs"])


def get_job_store() -> JobStore:
    return JobStore(get_redis(), get_settings().scaling_job_result_ttl_seconds)


@router.post(
    "/{conversation_id}/jobs",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_job(
    conversation_id: str,
    payload: ChatQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    store: JobStore = Depends(get_job_store),
):
    # Same ownership check as the synchronous endpoint, done up front so a
    # bad conversation id fails immediately instead of inside the worker.
    try:
        chat_service.get_owned_conversation(db, conversation_id, current_user.id)
    except chat_service.ConversationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    try:
        job_id = store.enqueue(current_user.id, conversation_id, payload.message)
    except RedisError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue unavailable")

    return JobSubmitResponse(job_id=job_id, status="queued")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    store: JobStore = Depends(get_job_store),
):
    try:
        job = store.get(job_id)
    except RedisError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue unavailable")

    # Same 404 for "doesn't exist" and "belongs to someone else", so job ids can't be probed.
    if job is None or job["user_id"] != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    result = ChatQueryResponse(**json.loads(job["result"])) if job["result"] else None
    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        conversation_id=job["conversation_id"],
        result=result,
        error=job["error"] or None,
    )