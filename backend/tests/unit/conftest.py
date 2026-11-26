# backend/tests/unit/conftest.py

import fakeredis
import pytest

from app.scaling.idempotency import IdempotencyStore
from app.scaling.jobs import JobStore
from app.scaling.rate_limit import RateLimiter
from app.scaling.budgets import UserTokenLedger


@pytest.fixture()
def fake_redis():
    # A fresh in-memory Redis per test, so tests never see each other's jobs.
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture()
def job_store(fake_redis):
    return JobStore(fake_redis, result_ttl_seconds=60)


@pytest.fixture()
def rate_limiter(fake_redis):
    # Very generous by default, so tests that aren't about rate limiting never hit it.
    return RateLimiter(fake_redis, capacity=1000, refill_per_second=1000)


@pytest.fixture()
def idempotency_store(fake_redis):
    return IdempotencyStore(fake_redis, ttl_seconds=3600)


@pytest.fixture()
def token_ledger(fake_redis):
    # Generous by default; tests about the daily budget build their own with a small limit.
    return UserTokenLedger(fake_redis, daily_limit=1_000_000)