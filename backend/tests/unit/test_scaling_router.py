# backend/tests/unit/test_scaling_router.py

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import get_settings
from app.db import crud
from app.db.session import get_db_session
from app.deps import get_current_user
from app.main import app as main_app
from app.scaling.budgets import UserTokenLedger
from app.scaling.jobs import JOB_KEY_PREFIX
from app.scaling.rate_limit import RateLimiter
from app.scaling.router import (
    get_idempotency_store,
    get_job_store,
    get_max_queue_depth,
    get_rate_limiter,
    get_token_ledger,
    router,
)


@pytest.fixture()
def users(db_session):
    alice = crud.create_user(db_session, username="alice", email="alice@example.com", hashed_password="x")
    bob = crud.create_user(db_session, username="bob", email="bob@example.com", hashed_password="x")
    return SimpleUsers(alice, bob)


class SimpleUsers:
    def __init__(self, alice, bob):
        self.alice = alice
        self.bob = bob


@pytest.fixture()
def scaling_client(db_session, job_store, rate_limiter, idempotency_store, token_ledger, users):
    """A tiny app that mounts ONLY the scaling router, with the database,
    job store, limits and login swapped for test doubles. Logged in as alice by default."""
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_db_session] = lambda: db_session
    test_app.dependency_overrides[get_job_store] = lambda: job_store
    test_app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
    test_app.dependency_overrides[get_idempotency_store] = lambda: idempotency_store
    test_app.dependency_overrides[get_token_ledger] = lambda: token_ledger    
    test_app.dependency_overrides[get_max_queue_depth] = lambda: 1000
    test_app.dependency_overrides[get_current_user] = lambda: users.alice
    return TestClient(test_app)


def _login_as(client, user):
    client.app.dependency_overrides[get_current_user] = lambda: user


def test_submit_returns_202_and_queues_job(scaling_client, db_session, job_store, users):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)

    response = scaling_client.post(f"/chat/{conversation.id}/jobs", json={"message": "hello"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert job_store.get(body["job_id"])["message"] == "hello"
    assert job_store.queue_depth() == 1


def test_submit_unknown_conversation_returns_404(scaling_client, job_store):
    response = scaling_client.post("/chat/does-not-exist/jobs", json={"message": "hello"})

    assert response.status_code == 404
    assert job_store.queue_depth() == 0


def test_submit_other_users_conversation_returns_404(scaling_client, db_session, job_store, users):
    conversation = crud.create_conversation(db_session, user_id=users.bob.id)

    response = scaling_client.post(f"/chat/{conversation.id}/jobs", json={"message": "hello"})

    assert response.status_code == 404
    assert job_store.queue_depth() == 0


def test_submit_returns_503_when_queue_is_down(scaling_client, db_session, job_store, users, monkeypatch):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)

    def _raise(*args, **kwargs):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(job_store, "enqueue", _raise)

    response = scaling_client.post(f"/chat/{conversation.id}/jobs", json={"message": "hello"})

    assert response.status_code == 503


def test_get_job_shows_queued_state(scaling_client, job_store, users):
    job_id = job_store.enqueue(users.alice.id, "conv-1", "hello")

    body = scaling_client.get(f"/chat/jobs/{job_id}").json()

    assert body["status"] == "queued"
    assert body["result"] is None
    assert body["error"] is None


def test_get_job_returns_result_when_succeeded(scaling_client, job_store, users):
    job_id = job_store.enqueue(users.alice.id, "conv-1", "hello")
    job_store.mark_succeeded(job_id, {
        "conversation_id": "conv-1",
        "role": "assistant",
        "content": "the answer",
        "media_filenames": [],
    })

    body = scaling_client.get(f"/chat/jobs/{job_id}").json()

    assert body["status"] == "succeeded"
    assert body["result"]["content"] == "the answer"


def test_get_job_returns_error_when_failed(scaling_client, job_store, users):
    job_id = job_store.enqueue(users.alice.id, "conv-1", "hello")
    job_store.mark_failed(job_id, "Conversation not found")

    body = scaling_client.get(f"/chat/jobs/{job_id}").json()

    assert body["status"] == "failed"
    assert body["error"] == "Conversation not found"


def test_get_job_of_another_user_returns_404(scaling_client, job_store, users):
    job_id = job_store.enqueue(users.bob.id, "conv-1", "hello")

    assert scaling_client.get(f"/chat/jobs/{job_id}").status_code == 404


def test_get_unknown_job_returns_404(scaling_client):
    assert scaling_client.get("/chat/jobs/no-such-job").status_code == 404


def test_job_routes_are_absent_when_scaling_is_disabled():
    if get_settings().scaling_enabled:
        pytest.skip("SCALING_ENABLED is on in this environment")

    paths = set(main_app.openapi()["paths"])
    assert "/chat/{conversation_id}/query" in paths  # the plain endpoint is still there
    assert "/chat/jobs/{job_id}" not in paths
    assert "/chat/{conversation_id}/jobs" not in paths


def test_repeated_idempotency_key_returns_the_original_job(scaling_client, db_session, job_store, users):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"
    headers = {"Idempotency-Key": "abc-123"}

    first = scaling_client.post(url, json={"message": "hello"}, headers=headers)
    second = scaling_client.post(url, json={"message": "hello"}, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert job_store.queue_depth() == 1  # no second job


def test_replay_does_not_use_a_rate_limit_token(scaling_client, db_session, users, fake_redis):
    scaling_client.app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(
        fake_redis, capacity=1, refill_per_second=0.5
    )
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"
    headers = {"Idempotency-Key": "abc-123"}

    assert scaling_client.post(url, json={"message": "hello"}, headers=headers).status_code == 202
    assert scaling_client.post(url, json={"message": "hello"}, headers=headers).status_code == 202  # not 429
    assert scaling_client.post(url, json={"message": "different"}).status_code == 429  # bucket really is empty


def test_same_key_with_a_different_message_is_rejected(scaling_client, db_session, job_store, users):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"
    headers = {"Idempotency-Key": "abc-123"}

    scaling_client.post(url, json={"message": "hello"}, headers=headers)
    response = scaling_client.post(url, json={"message": "something else"}, headers=headers)

    assert response.status_code == 422
    assert job_store.queue_depth() == 1


def test_same_key_from_different_users_makes_separate_jobs(scaling_client, db_session, job_store, users):
    alice_url = f"/chat/{crud.create_conversation(db_session, user_id=users.alice.id).id}/jobs"
    bob_url = f"/chat/{crud.create_conversation(db_session, user_id=users.bob.id).id}/jobs"
    headers = {"Idempotency-Key": "shared-key"}

    alice_job = scaling_client.post(alice_url, json={"message": "hello"}, headers=headers).json()["job_id"]
    _login_as(scaling_client, users.bob)
    bob_job = scaling_client.post(bob_url, json={"message": "hello"}, headers=headers).json()["job_id"]

    assert alice_job != bob_job
    assert job_store.queue_depth() == 2


def test_expired_original_job_is_replaced_by_a_new_one(scaling_client, db_session, job_store, users, fake_redis):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"
    headers = {"Idempotency-Key": "abc-123"}

    first = scaling_client.post(url, json={"message": "hello"}, headers=headers).json()["job_id"]
    fake_redis.delete(f"{JOB_KEY_PREFIX}{first}")  # the original job record has expired

    second = scaling_client.post(url, json={"message": "hello"}, headers=headers)

    assert second.status_code == 202
    assert second.json()["job_id"] != first
    assert job_store.get(second.json()["job_id"]) is not None


def test_failed_enqueue_releases_the_key_so_a_retry_works(scaling_client, db_session, job_store, users, monkeypatch):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"
    headers = {"Idempotency-Key": "abc-123"}

    def _raise(*args, **kwargs):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(job_store, "enqueue", _raise)
    assert scaling_client.post(url, json={"message": "hello"}, headers=headers).status_code == 503
    monkeypatch.undo()

    retry = scaling_client.post(url, json={"message": "hello"}, headers=headers)

    assert retry.status_code == 202
    assert job_store.get(retry.json()["job_id"]) is not None


def test_overlong_idempotency_key_is_rejected(scaling_client, db_session, users):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)

    response = scaling_client.post(
        f"/chat/{conversation.id}/jobs", json={"message": "hello"}, headers={"Idempotency-Key": "x" * 200}
    )

    assert response.status_code == 422


def test_requests_without_a_key_always_make_new_jobs(scaling_client, db_session, job_store, users):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"

    first = scaling_client.post(url, json={"message": "hello"}).json()["job_id"]
    second = scaling_client.post(url, json={"message": "hello"}).json()["job_id"]

    assert first != second
    assert job_store.queue_depth() == 2

def test_submit_is_rate_limited_with_429_and_retry_after(scaling_client, db_session, job_store, users, fake_redis):
    scaling_client.app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(
        fake_redis, capacity=2, refill_per_second=0.5
    )
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"

    assert scaling_client.post(url, json={"message": "one"}).status_code == 202
    assert scaling_client.post(url, json={"message": "two"}).status_code == 202
    response = scaling_client.post(url, json={"message": "three"})

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert job_store.queue_depth() == 2  # the refused request was not queued


def test_rate_limit_is_per_user(scaling_client, db_session, users, fake_redis):
    scaling_client.app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(
        fake_redis, capacity=1, refill_per_second=0.5
    )
    alice_url = f"/chat/{crud.create_conversation(db_session, user_id=users.alice.id).id}/jobs"
    bob_url = f"/chat/{crud.create_conversation(db_session, user_id=users.bob.id).id}/jobs"

    assert scaling_client.post(alice_url, json={"message": "one"}).status_code == 202
    assert scaling_client.post(alice_url, json={"message": "two"}).status_code == 429

    _login_as(scaling_client, users.bob)
    assert scaling_client.post(bob_url, json={"message": "one"}).status_code == 202


def test_submit_sheds_load_when_queue_is_full(scaling_client, db_session, job_store, users):
    scaling_client.app.dependency_overrides[get_max_queue_depth] = lambda: 1
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    url = f"/chat/{conversation.id}/jobs"

    assert scaling_client.post(url, json={"message": "one"}).status_code == 202
    response = scaling_client.post(url, json={"message": "two"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert job_store.queue_depth() == 1  # the second job was not queued


def test_submit_is_refused_when_the_daily_token_budget_is_used_up(scaling_client, db_session, job_store, users, fake_redis):
    ledger = UserTokenLedger(fake_redis, daily_limit=100)
    ledger.add(users.alice.id, 100)
    scaling_client.app.dependency_overrides[get_token_ledger] = lambda: ledger
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)

    response = scaling_client.post(f"/chat/{conversation.id}/jobs", json={"message": "hello"})

    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 24 * 3600
    assert "Daily usage limit" in response.json()["detail"]
    assert job_store.queue_depth() == 0


def test_daily_budget_is_per_user(scaling_client, db_session, users, fake_redis):
    ledger = UserTokenLedger(fake_redis, daily_limit=100)
    ledger.add(users.alice.id, 100)  # alice is out, bob is not
    scaling_client.app.dependency_overrides[get_token_ledger] = lambda: ledger
    bob_url = f"/chat/{crud.create_conversation(db_session, user_id=users.bob.id).id}/jobs"

    _login_as(scaling_client, users.bob)

    assert scaling_client.post(bob_url, json={"message": "hello"}).status_code == 202


def test_get_job_returns_usage_when_recorded(scaling_client, job_store, users):
    job_id = job_store.enqueue(users.alice.id, "c1", "hello")
    job_store.mark_failed(job_id, "limit", usage={"tokens": 120, "llm_calls": 3})

    body = scaling_client.get(f"/chat/jobs/{job_id}").json()

    assert body["usage"] == {"tokens": 120, "llm_calls": 3}


def test_get_job_has_no_usage_before_it_finishes(scaling_client, job_store, users):
    job_id = job_store.enqueue(users.alice.id, "c1", "hello")

    assert scaling_client.get(f"/chat/jobs/{job_id}").json()["usage"] is None




def _submits(outcome):
    return REGISTRY.get_sample_value("adaptiverag_submit_requests_total", {"outcome": outcome})


def test_accepted_submit_is_counted(scaling_client, db_session, users):
    conversation = crud.create_conversation(db_session, user_id=users.alice.id)
    before = _submits("accepted")

    scaling_client.post(f"/chat/{conversation.id}/jobs", json={"message": "hello"})

    assert _submits("accepted") == before + 1


def test_rate_limited_submit_is_counted(scaling_client, db_session, users, fake_redis):
    scaling_client.app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(
        fake_redis, capacity=1, refill_per_second=0.5
    )
    url = f"/chat/{crud.create_conversation(db_session, user_id=users.alice.id).id}/jobs"
    scaling_client.post(url, json={"message": "one"})
    before = _submits("rate_limited")

    scaling_client.post(url, json={"message": "two"})

    assert _submits("rate_limited") == before + 1


def test_budget_exhausted_submit_is_counted(scaling_client, db_session, users, fake_redis):
    ledger = UserTokenLedger(fake_redis, daily_limit=100)
    ledger.add(users.alice.id, 100)
    scaling_client.app.dependency_overrides[get_token_ledger] = lambda: ledger
    url = f"/chat/{crud.create_conversation(db_session, user_id=users.alice.id).id}/jobs"
    before = _submits("budget_exhausted")

    scaling_client.post(url, json={"message": "hello"})

    assert _submits("budget_exhausted") == before + 1


def test_shed_submit_is_counted(scaling_client, db_session, users):
    scaling_client.app.dependency_overrides[get_max_queue_depth] = lambda: 1
    url = f"/chat/{crud.create_conversation(db_session, user_id=users.alice.id).id}/jobs"
    scaling_client.post(url, json={"message": "one"})
    before = _submits("shed")

    scaling_client.post(url, json={"message": "two"})

    assert _submits("shed") == before + 1


def test_not_found_submit_is_counted(scaling_client):
    before = _submits("not_found")

    scaling_client.post("/chat/does-not-exist/jobs", json={"message": "hello"})

    assert _submits("not_found") == before + 1


def test_replayed_and_conflicting_submits_are_counted(scaling_client, db_session, users):
    url = f"/chat/{crud.create_conversation(db_session, user_id=users.alice.id).id}/jobs"
    headers = {"Idempotency-Key": "abc-123"}
    scaling_client.post(url, json={"message": "hello"}, headers=headers)
    replayed, conflict = _submits("replayed"), _submits("conflict")

    scaling_client.post(url, json={"message": "hello"}, headers=headers)
    scaling_client.post(url, json={"message": "something else"}, headers=headers)

    assert _submits("replayed") == replayed + 1
    assert _submits("conflict") == conflict + 1


def test_unavailable_queue_is_counted(scaling_client, db_session, job_store, users, monkeypatch):
    url = f"/chat/{crud.create_conversation(db_session, user_id=users.alice.id).id}/jobs"

    def _raise(*args, **kwargs):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(job_store, "enqueue", _raise)
    before = _submits("unavailable")

    scaling_client.post(url, json={"message": "hello"})

    assert _submits("unavailable") == before + 1

