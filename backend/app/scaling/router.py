# backend/app/scaling/router.py

"""
Queue-based chat endpoints. Mounted by main.py only when SCALING_ENABLED=true.

POST /chat/{conversation_id}/jobs  -> 202 + job_id (returns immediately)
GET  /chat/jobs/{job_id}           -> status, and the answer once finished

Submissions are protected in this order, cheapest check first:
  1. per-user rate limit      -> 429 + Retry-After   (this user is sending too much)
  2. queue depth limit        -> 503 + Retry-After   (the system is full: load shedding)
  3. conversation ownership   -> 404                 (needs the database)
"""

import json
import math

from fastapi import APIRouter, Depends, HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.api.schemas.chat import ChatQueryRequest, ChatQueryResponse
from app.config import get_settings
from app.db.models import User
from app.deps import get_current_user, get_db
from app.scaling.jobs import JobStore
from app.scaling.rate_limit import RateLimiter
from app.scaling.redis_client import get_redis
from app.scaling.schemas import JobStatusResponse, JobSubmitResponse
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["chat-jobs"])

# How long a client is told to wait when the queue is full.
SHED_RETRY_AFTER_SECONDS = 5


def get_job_store() -> JobStore:
    return JobStore(get_redis(), get_settings().scaling_job_result_ttl_seconds)


def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(
        get_redis(),
        capacity=settings.scaling_rate_limit_capacity,
        refill_per_second=settings.scaling_rate_limit_refill_per_second,
    )


def get_max_queue_depth() -> int:
    return get_settings().scaling_queue_max_depth


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
    limiter: RateLimiter = Depends(get_rate_limiter),
    max_queue_depth: int = Depends(get_max_queue_depth),
):
    try:
        limit = limiter.check(current_user.id)
        queue_depth = store.queue_depth()
    except RedisError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue unavailable")

    if not limit.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(1, math.ceil(limit.retry_after_seconds)))},
        )

    # Load shedding: when the queue is full, refuse new work instead of letting
    # it pile up. The check is approximate (a few extra jobs can slip in while
    # several requests race), which is fine for a safety valve.
    if queue_depth >= max_queue_depth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server busy, please try again shortly",
            headers={"Retry-After": str(SHED_RETRY_AFTER_SECONDS)},
        )

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