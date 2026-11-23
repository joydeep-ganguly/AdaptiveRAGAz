# backend/tests/unit/conftest.py

import fakeredis
import pytest

from app.scaling.jobs import JobStore


@pytest.fixture()
def fake_redis():
    # A fresh in-memory Redis per test, so tests never see each other's jobs.
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture()
def job_store(fake_redis):
    return JobStore(fake_redis, result_ttl_seconds=60)