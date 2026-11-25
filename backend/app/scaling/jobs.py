# backend/app/scaling/jobs.py

"""
Job store + queue on Redis.

- The queue is a Redis list holding job ids (new jobs pushed on the left,
  workers take from the right, so the oldest job is served first).
- Each job is a Redis hash at adaptiverag:job:<id> with an expiry, so
  finished jobs clean themselves up.
"""

import json
import time
import uuid

QUEUE_KEY = "adaptiverag:queue"
JOB_KEY_PREFIX = "adaptiverag:job:"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


class JobStore:
    def __init__(self, client, result_ttl_seconds: int):
        # The Redis client is passed in (not created here) so tests can give
        # it a fake in-memory Redis.
        self.client = client
        self.result_ttl_seconds = result_ttl_seconds

    def _key(self, job_id: str) -> str:
        return f"{JOB_KEY_PREFIX}{job_id}"

    def enqueue(self, user_id: int, conversation_id: str, message: str, job_id: str | None = None) -> str:
        # job_id can be supplied by the caller (the submit endpoint creates it
        # before claiming an idempotency key); otherwise a new one is made.
        job_id = job_id or str(uuid.uuid4())
        now = str(time.time())

        # One pipeline = one atomic transaction: the job record is guaranteed
        # to exist before its id becomes visible in the queue.
        pipe = self.client.pipeline()
        pipe.hset(self._key(job_id), mapping={
            "status": STATUS_QUEUED,
            "user_id": str(user_id),
            "conversation_id": conversation_id,
            "message": message,
            "result": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
        })
        pipe.expire(self._key(job_id), self.result_ttl_seconds)
        pipe.lpush(QUEUE_KEY, job_id)
        pipe.execute()
        return job_id

    def get(self, job_id: str) -> dict | None:
        data = self.client.hgetall(self._key(job_id))
        return data or None

    def pop(self, timeout_seconds: int = 5) -> str | None:
        """Wait up to timeout_seconds for the next job id. Returns None if nothing arrived."""
        item = self.client.brpop(QUEUE_KEY, timeout=timeout_seconds)
        return item[1] if item else None

    def queue_depth(self) -> int:
        return self.client.llen(QUEUE_KEY)

    def mark_running(self, job_id: str) -> None:
        self._update(job_id, status=STATUS_RUNNING)

    def mark_succeeded(self, job_id: str, result: dict) -> None:
        self._update(job_id, status=STATUS_SUCCEEDED, result=json.dumps(result))
        # Keep the finished result for the full TTL, counted from completion.
        self.client.expire(self._key(job_id), self.result_ttl_seconds)

    def mark_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, status=STATUS_FAILED, error=error)
        self.client.expire(self._key(job_id), self.result_ttl_seconds)

    def _update(self, job_id: str, **fields: str) -> None:
        fields["updated_at"] = str(time.time())
        self.client.hset(self._key(job_id), mapping=fields)