# backend/app/scaling/router.py

"""
Queue-based chat endpoints. Mounted by main.py only when SCALING_ENABLED=true.

POST /chat/{conversation_id}/jobs  -> 202 + job_id (returns immediately)
GET  /chat/jobs/{job_id}           -> status, and the answer once finished

Submissions are handled in this order, cheapest check first:
  0. repeated Idempotency-Key -> the original job is returned (no new work, no rate-limit token used)
  1. per-user rate limit      -> 429 + Retry-After   (this user is sending too much)
  2. queue depth limit        -> 503 + Retry-After   (the system is full: load shedding)
  3. conversation ownership   -> 404                 (needs the database)
"""

import json
import math
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.api.schemas.chat import ChatQueryRequest, ChatQueryResponse
from app.config import get_settings
from app.db.models import User
from app.deps import get_current_user, get_db
from app.scaling.idempotency import IdempotencyStore, request_fingerprint
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


def get_idempotency_store() -> IdempotencyStore:
    return IdempotencyStore(get_redis(), get_settings().scaling_idempotency_ttl_seconds)


def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return RateLimiter(
        get_redis(),
        capacity=settings.scaling_rate_limit_capacity,
        refill_per_second=settings.scaling_rate_limit_refill_per_second,
    )


def get_max_queue_depth() -> int:
    return get_settings().scaling_queue_max_depth


def _replay_of_existing_job(
    entry, fingerprint, store, response, job_may_be_pending: bool = False
) -> JobSubmitResponse | None:
    """
    A repeated Idempotency-Key: hand back the original job.

    If the original job record is gone it has expired, and None is returned so the
    caller can treat this as a new request. The one exception is a request that
    lost a race to claim the key: the winner may not have queued its job yet, so
    the job is reported as "queued" (job_may_be_pending=True) rather than duplicated.
    """
    if entry.fingerprint != fingerprint:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key was already used with a different request",
        )
    job = store.get(entry.job_id)
    if job is None and not job_may_be_pending:
        return None
    response.headers["Idempotent-Replayed"] = "true"
    return JobSubmitResponse(job_id=entry.job_id, status=job["status"] if job else "queued")


@router.post(
    "/{conversation_id}/jobs",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_job(
    conversation_id: str,
    payload: ChatQueryRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    store: JobStore = Depends(get_job_store),
    limiter: RateLimiter = Depends(get_rate_limiter),
    max_queue_depth: int = Depends(get_max_queue_depth),
    idempotency: IdempotencyStore = Depends(get_idempotency_store),
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
):
    fingerprint = request_fingerprint(conversation_id, payload.message)

    # A repeated key is answered first: it is the same request, so it must not
    # use a rate-limit token, be shed, or queue a second job.
    if idempotency_key:
        try:
            existing = idempotency.lookup(current_user.id, idempotency_key)
            if existing is not None:
                replay = _replay_of_existing_job(existing, fingerprint, store, response)
                if replay is not None:
                    return replay
                idempotency.release(current_user.id, idempotency_key)  # original job expired: start fresh
        except RedisError:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue unavailable")

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

    job_id = str(uuid.uuid4())

    if idempotency_key:
        try:
            # Claim the key before queueing. If another request with the same key
            # got here first (two simultaneous retries), return its job instead.
            existing = idempotency.claim(current_user.id, idempotency_key, job_id, fingerprint)
            if existing is not None:
                return _replay_of_existing_job(existing, fingerprint, store, response, job_may_be_pending=True)
        except RedisError:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue unavailable")

    try:
        store.enqueue(current_user.id, conversation_id, payload.message, job_id=job_id)
    except RedisError:
        if idempotency_key:
            try:
                idempotency.release(current_user.id, idempotency_key)  # let the client's retry through
            except RedisError:
                pass
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