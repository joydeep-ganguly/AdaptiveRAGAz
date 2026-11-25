# backend/app/scaling/idempotency.py

"""
Idempotency keys for job submission.

A client that is unsure whether its submit request arrived (timeout, dropped
connection, double click) can send the same `Idempotency-Key` header again and
get the ORIGINAL job back, instead of creating a second job and paying for a
second run of the agent.

Keys are stored per user, so two users can never collide or probe each other's
keys. Each key remembers the job id and a fingerprint of the request, so the
same key reused for a different message can be detected and rejected.
"""

import hashlib
from dataclasses import dataclass

IDEMPOTENCY_KEY_PREFIX = "adaptiverag:idem:"


@dataclass(frozen=True)
class IdempotencyEntry:
    job_id: str
    fingerprint: str


def request_fingerprint(conversation_id: str, message: str) -> str:
    return hashlib.sha256(f"{conversation_id}\n{message}".encode("utf-8")).hexdigest()


class IdempotencyStore:
    def __init__(self, client, ttl_seconds: int):
        self.client = client
        self.ttl_seconds = ttl_seconds

    def _key(self, user_id: int, idempotency_key: str) -> str:
        return f"{IDEMPOTENCY_KEY_PREFIX}{user_id}:{idempotency_key}"

    def lookup(self, user_id: int, idempotency_key: str) -> IdempotencyEntry | None:
        value = self.client.get(self._key(user_id, idempotency_key))
        if value is None:
            return None
        job_id, fingerprint = value.split(":", 1)
        return IdempotencyEntry(job_id=job_id, fingerprint=fingerprint)

    def claim(self, user_id: int, idempotency_key: str, job_id: str, fingerprint: str) -> IdempotencyEntry | None:
        """
        Try to attach this key to `job_id`. Returns None if we won (the key now
        points at our job). Returns the existing entry if another request got
        there first. SET NX is atomic, so two simultaneous retries cannot both win.
        """
        key = self._key(user_id, idempotency_key)
        for _ in range(2):
            if self.client.set(key, f"{job_id}:{fingerprint}", nx=True, ex=self.ttl_seconds):
                return None
            existing = self.lookup(user_id, idempotency_key)
            if existing is not None:
                return existing
            # The key expired between the two calls: try to claim it once more.
        return None  # vanished twice in a row (extremely unlikely): proceed without dedup

    def release(self, user_id: int, idempotency_key: str) -> None:
        self.client.delete(self._key(user_id, idempotency_key))