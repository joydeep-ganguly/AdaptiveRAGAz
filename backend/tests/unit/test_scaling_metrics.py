# backend/tests/unit/test_scaling_metrics.py

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from redis.exceptions import RedisError

from app.scaling import metrics
from app.scaling.cache import STATS_KEY_PREFIX
from app.scaling.metrics_router import get_metrics_redis, router


def _text(client) -> str:
    return metrics.render_metrics(client).decode()


def test_queue_depth_is_read_from_redis(fake_redis, job_store):
    for n in range(3):
        job_store.enqueue(1, "c1", f"question {n}")

    assert "adaptiverag_queue_depth 3.0" in _text(fake_redis)


def test_queue_depth_is_zero_when_the_queue_is_empty(fake_redis):
    assert "adaptiverag_queue_depth 0.0" in _text(fake_redis)


def test_cache_hits_and_misses_are_reported(fake_redis):
    fake_redis.set(f"{STATS_KEY_PREFIX}llm:hit", 7)
    fake_redis.set(f"{STATS_KEY_PREFIX}llm:miss", 2)

    text = _text(fake_redis)

    assert 'adaptiverag_cache_requests_total{cache="llm",outcome="hit"} 7.0' in text
    assert 'adaptiverag_cache_requests_total{cache="llm",outcome="miss"} 2.0' in text
    assert 'adaptiverag_cache_requests_total{cache="embedding",outcome="hit"} 0.0' in text


def test_a_redis_outage_skips_the_state_numbers_but_not_the_scrape():
    class DownRedis:
        def llen(self, key):
            raise RedisError("down")

    text = _text(DownRedis())

    assert "adaptiverag_queue_depth" not in text
    assert "adaptiverag_jobs_total" in text  # this process's own numbers are still there


def test_every_outcome_is_visible_from_the_start():
    for outcome in metrics.SUBMIT_OUTCOMES:
        assert REGISTRY.get_sample_value("adaptiverag_submit_requests_total", {"outcome": outcome}) is not None
    for outcome in metrics.JOB_OUTCOMES:
        assert REGISTRY.get_sample_value("adaptiverag_jobs_total", {"outcome": outcome}) is not None


def test_no_metric_is_labelled_with_a_user_or_job():
    labels = {"outcome", "cache"}
    for family in REGISTRY.collect():
        if family.name.startswith("adaptiverag_"):
            for sample in family.samples:
                assert set(sample.labels) - {"le"} <= labels


def test_metrics_endpoint_serves_the_prometheus_text_format(fake_redis, job_store):
    job_store.enqueue(1, "c1", "hello")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_metrics_redis] = lambda: fake_redis

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "adaptiverag_queue_depth 1.0" in response.text
    assert "# TYPE adaptiverag_job_duration_seconds histogram" in response.text


def test_a_busy_worker_port_only_loses_metrics(monkeypatch):
    def _busy(port):
        raise OSError("address already in use")

    monkeypatch.setattr(metrics, "start_http_server", _busy)

    assert metrics.start_worker_metrics_server(9100) is False


def test_worker_metrics_server_starts_on_the_given_port(monkeypatch):
    started = []
    monkeypatch.setattr(metrics, "start_http_server", started.append)

    assert metrics.start_worker_metrics_server(9100) is True
    assert started == [9100]