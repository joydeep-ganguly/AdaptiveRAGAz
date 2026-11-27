# backend/tests/unit/test_scaling_worker.py

import json
import time
from types import SimpleNamespace

from prometheus_client import REGISTRY

from app.db import crud
from app.scaling import worker
from app.scaling.budgets import BudgetLimits, UserTokenLedger

class MeteredGraph:
    """Behaves like the real graph under a budget: each model call fires the
    callbacks that BudgetedGraph adds to the config."""

    def __init__(self, calls=1, tokens_per_call=100):
        self.calls = calls
        self.tokens_per_call = tokens_per_call

    def get_state(self, config=None):
        return SimpleNamespace(values={"messages": []})

    def invoke(self, input, config=None):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult

        callback = config["callbacks"][0]
        for _ in range(self.calls):
            callback.on_chat_model_start({}, [[]])
            message = AIMessage(content="ok", usage_metadata={
                "input_tokens": self.tokens_per_call, "output_tokens": 0, "total_tokens": self.tokens_per_call,
            })
            callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
        return {"messages": [AIMessage(content="a metered answer")]}


def _budget(fake_redis, max_tokens=1000, max_llm_calls=10, daily=10_000):
    return BudgetLimits(UserTokenLedger(fake_redis, daily), max_tokens, max_llm_calls)


def test_job_with_budget_records_usage_and_charges_the_user(db_session, job_store, fake_redis, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")
    budget = _budget(fake_redis)

    worker.process_job(job_store, MeteredGraph(calls=3, tokens_per_call=100), job_id, budget)

    job = job_store.get(job_id)
    assert job["status"] == "succeeded"
    assert json.loads(job["usage"]) == {"tokens": 300, "llm_calls": 3}
    assert budget.ledger.used_today(user_id) == 300


def test_job_over_its_token_budget_fails_with_a_clear_message(db_session, job_store, fake_redis, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")

    worker.process_job(job_store, MeteredGraph(calls=5, tokens_per_call=100), job_id, _budget(fake_redis, max_tokens=250))

    job = job_store.get(job_id)
    assert job["status"] == "failed"
    assert "per-question limit" in job["error"]
    assert json.loads(job["usage"])["tokens"] == 300  # stopped before call 4; overshoot is at most one call


def test_job_over_its_call_budget_fails(db_session, job_store, fake_redis, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")

    worker.process_job(job_store, MeteredGraph(calls=5, tokens_per_call=1), job_id, _budget(fake_redis, max_llm_calls=2))

    job = job_store.get(job_id)
    assert job["status"] == "failed"
    assert "more steps" in job["error"]


def test_job_fails_when_the_users_daily_budget_is_gone(db_session, job_store, fake_redis, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")
    budget = _budget(fake_redis, daily=100)
    budget.ledger.add(user_id, 100)

    worker.process_job(job_store, MeteredGraph(), job_id, budget)

    job = job_store.get(job_id)
    assert job["status"] == "failed"
    assert "daily usage limit" in job["error"]


def test_job_without_a_budget_is_unchanged(db_session, job_store, fake_graph, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")

    worker.process_job(job_store, fake_graph, job_id)

    assert job_store.get(job_id)["status"] == "succeeded"


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



def _value(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_successful_job_is_measured(db_session, job_store, fake_redis, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")
    done = _value("adaptiverag_jobs_total", {"outcome": "succeeded"})
    runs = _value("adaptiverag_job_duration_seconds_count")
    waits = _value("adaptiverag_job_queue_wait_seconds_count")
    tokens = _value("adaptiverag_job_tokens_sum")
    calls = _value("adaptiverag_job_llm_calls_sum")

    worker.process_job(job_store, MeteredGraph(calls=3, tokens_per_call=100), job_id, _budget(fake_redis))

    assert _value("adaptiverag_jobs_total", {"outcome": "succeeded"}) == done + 1
    assert _value("adaptiverag_job_duration_seconds_count") == runs + 1
    assert _value("adaptiverag_job_queue_wait_seconds_count") == waits + 1
    assert _value("adaptiverag_job_tokens_sum") == tokens + 300
    assert _value("adaptiverag_job_llm_calls_sum") == calls + 3


def test_budget_stopped_job_is_counted_with_its_tokens(db_session, job_store, fake_redis, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")
    stopped = _value("adaptiverag_jobs_total", {"outcome": "budget_stopped"})
    tokens = _value("adaptiverag_job_tokens_sum")

    worker.process_job(job_store, MeteredGraph(calls=5, tokens_per_call=100), job_id, _budget(fake_redis, max_tokens=250))

    assert _value("adaptiverag_jobs_total", {"outcome": "budget_stopped"}) == stopped + 1
    assert _value("adaptiverag_job_tokens_sum") == tokens + 300


def test_failed_job_is_counted_and_the_in_progress_gauge_returns_to_its_start(db_session, job_store, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")
    failed = _value("adaptiverag_jobs_total", {"outcome": "failed"})
    in_progress = _value("adaptiverag_jobs_in_progress")
    token_runs = _value("adaptiverag_job_tokens_count")

    worker.process_job(job_store, ExplodingGraph(), job_id)  # no budget, so no token numbers

    assert _value("adaptiverag_jobs_total", {"outcome": "failed"}) == failed + 1
    assert _value("adaptiverag_jobs_in_progress") == in_progress
    assert _value("adaptiverag_job_tokens_count") == token_runs


def test_queue_wait_is_measured_from_the_time_of_submit(db_session, job_store, fake_graph, monkeypatch):
    monkeypatch.setattr(worker, "SessionLocal", lambda: db_session)
    user_id, conversation_id = _user_and_conversation(db_session, "alice")
    job_id = job_store.enqueue(user_id, conversation_id, "hello")
    job_store.client.hset(f"adaptiverag:job:{job_id}", "created_at", str(time.time() - 5))
    waited = _value("adaptiverag_job_queue_wait_seconds_sum")

    worker.process_job(job_store, fake_graph, job_id)

    assert _value("adaptiverag_job_queue_wait_seconds_sum") - waited >= 5


def test_an_expired_job_records_nothing(job_store, fake_graph):
    done = _value("adaptiverag_job_duration_seconds_count")

    worker.process_job(job_store, fake_graph, "no-such-job")

    assert _value("adaptiverag_job_duration_seconds_count") == done