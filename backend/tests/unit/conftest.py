# backend/tests/unit/conftest.py

import fakeredis
import pytest

from app.scaling.jobs import JobStore
from app.scaling.rate_limit import RateLimiter


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