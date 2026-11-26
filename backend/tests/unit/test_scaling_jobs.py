# backend/tests/unit/test_scaling_jobs.py

import json

from app.scaling.jobs import JOB_KEY_PREFIX


def test_enqueue_creates_queued_job_and_queues_id(job_store):
    job_id = job_store.enqueue(user_id=7, conversation_id="conv-1", message="hello")

    job = job_store.get(job_id)
    assert job["status"] == "queued"
    assert job["user_id"] == "7"
    assert job["conversation_id"] == "conv-1"
    assert job["message"] == "hello"
    assert job_store.queue_depth() == 1


def test_pop_returns_oldest_job_first(job_store):
    first = job_store.enqueue(1, "conv-1", "first")
    second = job_store.enqueue(1, "conv-1", "second")

    assert job_store.pop(timeout_seconds=1) == first
    assert job_store.pop(timeout_seconds=1) == second
    assert job_store.queue_depth() == 0


def test_pop_returns_none_when_queue_is_empty(job_store):
    assert job_store.pop(timeout_seconds=1) is None


def test_mark_succeeded_stores_result(job_store):
    job_id = job_store.enqueue(1, "conv-1", "hello")
    job_store.mark_running(job_id)
    assert job_store.get(job_id)["status"] == "running"

    job_store.mark_succeeded(job_id, {"content": "hi", "media_filenames": []})

    job = job_store.get(job_id)
    assert job["status"] == "succeeded"
    assert json.loads(job["result"]) == {"content": "hi", "media_filenames": []}


def test_mark_failed_stores_error(job_store):
    job_id = job_store.enqueue(1, "conv-1", "hello")

    job_store.mark_failed(job_id, "something went wrong")

    job = job_store.get(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "something went wrong"


def test_job_has_an_expiry(job_store, fake_redis):
    job_id = job_store.enqueue(1, "conv-1", "hello")
    assert 0 < fake_redis.ttl(f"{JOB_KEY_PREFIX}{job_id}") <= 60


def test_usage_is_stored_with_the_outcome(job_store):
    ok = job_store.enqueue(1, "c1", "hello")
    job_store.mark_succeeded(ok, {"content": "hi"}, usage={"tokens": 10, "llm_calls": 1})
    bad = job_store.enqueue(1, "c1", "hello")
    job_store.mark_failed(bad, "nope", usage={"tokens": 5, "llm_calls": 2})

    assert json.loads(job_store.get(ok)["usage"]) == {"tokens": 10, "llm_calls": 1}
    assert json.loads(job_store.get(bad)["usage"]) == {"tokens": 5, "llm_calls": 2}