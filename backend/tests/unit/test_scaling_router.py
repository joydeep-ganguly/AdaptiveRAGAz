# backend/tests/unit/test_scaling_router.py

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import get_settings
from app.db import crud
from app.db.session import get_db_session
from app.deps import get_current_user
from app.main import app as main_app
from app.scaling.router import get_job_store, router


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
def scaling_client(db_session, job_store, users):
    """A tiny app that mounts ONLY the scaling router, with the database,
    job store and login swapped for test doubles. Logged in as alice by default."""
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_db_session] = lambda: db_session
    test_app.dependency_overrides[get_job_store] = lambda: job_store
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