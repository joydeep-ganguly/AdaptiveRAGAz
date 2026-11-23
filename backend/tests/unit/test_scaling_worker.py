# backend/tests/unit/test_scaling_worker.py

import json
from types import SimpleNamespace

from app.db import crud
from app.scaling import worker


def _user_and_conversation(db_session, name):
    user = crud.create_user(db_session, username=name, email=f"{name}@example.com", hashed_password="x")
    conversation = crud.create_conversation(db_session, user_id=user.id)
    return user.id, conversation.id


class ExplodingGraph:
    """A graph whose run fails, to check the worker hides internal details."""

    def get_state(self, config=None):
        return SimpleNamespace(values={"messages": []})

    def invoke(self, input, config=None):
        raise RuntimeError("secret internal detail")


def test_process_job_success(db_session, job_store, fake_graph, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")

    worker.process_job(job_store, fake_graph, job_id)

    job = job_store.get(job_id)
    assert job["status"] == "succeeded"
    assert json.loads(job["result"])["content"] == "This is a fake agent reply."
    assert len(fake_graph.invocations) == 1


def test_process_job_unknown_conversation_fails(db_session, job_store, fake_graph, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, _ = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, "does-not-exist", "hello")

    worker.process_job(job_store, fake_graph, job_id)

    job = job_store.get(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "Conversation not found"
    assert fake_graph.invocations == []


def test_process_job_other_users_conversation_fails(db_session, job_store, fake_graph, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    _, alice_conversation = _user_and_conversation(db_session, "alice")
    bob_id, _ = _user_and_conversation(db_session, "bob")
    job_id = job_store.enqueue(bob_id, alice_conversation, "hello")

    worker.process_job(job_store, fake_graph, job_id)

    assert job_store.get(job_id)["status"] == "failed"
    assert fake_graph.invocations == []


def test_process_job_unexpected_error_hides_details(db_session, job_store, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")

    worker.process_job(job_store, ExplodingGraph(), job_id)

    job = job_store.get(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "Internal error while processing the question"
    assert "secret" not in job["error"]


def test_process_job_skips_expired_job(job_store, fake_graph):
    worker.process_job(job_store, fake_graph, "no-such-job")  # must not raise

    assert fake_graph.invocations == []