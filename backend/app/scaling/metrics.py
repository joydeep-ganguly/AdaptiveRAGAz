# backend/app/scaling/metrics.py

"""
Metrics for the scale profile, in the Prometheus text format.

Two kinds of numbers:
  * Events are counted where they happen, inside each process: the API counts
    submissions by outcome; the worker records how long each job waited and ran,
    and how many tokens it used. Each process serves its own numbers on /metrics.
  * State is read from Redis when /metrics is scraped: queue depth and cache
    hits and misses. Redis is shared, so every API instance reports the same value.

Rules that keep metrics cheap and safe:
  * No label ever holds a user id, job id or question. A label with many different
    values makes the number of time series grow without limit.
  * Metrics never break a request: if Redis is down, the state numbers are skipped.
"""

import logging

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from redis.exceptions import RedisError

from app.scaling.cache import cache_stats
from app.scaling.jobs import QUEUE_KEY

logger = logging.getLogger(__name__)

# Outcomes of POST /chat/{id}/jobs.
SUBMIT_OUTCOMES = (
    "accepted",           # 202, job queued
    "replayed",           # 202, repeated Idempotency-Key, original job returned
    "rate_limited",       # 429, per-user rate limit
    "budget_exhausted",   # 429, daily token budget used up
    "shed",               # 503, queue full
    "not_found",          # 404, conversation not found
    "conflict",           # 422, Idempotency-Key reused with a different request
    "unavailable",        # 503, Redis not reachable
)

# How a job ended in the worker.
JOB_OUTCOMES = ("succeeded", "failed", "budget_stopped")

SUBMITS = Counter(
    "adaptiverag_submit_requests_total",
    "Job submissions, by outcome",
    ["outcome"],
)
JOBS = Counter(
    "adaptiverag_jobs_total",
    "Jobs finished by the worker, by outcome",
    ["outcome"],
)
JOBS_IN_PROGRESS = Gauge(
    "adaptiverag_jobs_in_progress",
    "Jobs this worker is running right now",
)
QUEUE_WAIT = Histogram(
    "adaptiverag_job_queue_wait_seconds",
    "Time from submit to the worker starting the job",
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
JOB_DURATION = Histogram(
    "adaptiverag_job_duration_seconds",
    "Time the worker spent running a job",
    buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300),
)
JOB_TOKENS = Histogram(
    "adaptiverag_job_tokens",
    "Model tokens used by one job",
    buckets=(1000, 2500, 5000, 10000, 20000, 30000, 50000, 75000, 100000),
)
JOB_LLM_CALLS = Histogram(
    "adaptiverag_job_llm_calls",
    "Model calls made by one job",
    buckets=(1, 2, 3, 5, 7, 10, 15, 20, 30),
)

# Create every label combination now, so a counter that has not happened yet
# shows as 0 instead of being missing (graphs and alerts need the zero).
for _outcome in SUBMIT_OUTCOMES:
    SUBMITS.labels(outcome=_outcome)
for _outcome in JOB_OUTCOMES:
    JOBS.labels(outcome=_outcome)


class RedisStateCollector:
    """Reads shared state from Redis each time /metrics is scraped."""

    def __init__(self, client):
        self.client = client

    def collect(self):
        try:
            depth = self.client.llen(QUEUE_KEY)
            stats = cache_stats(self.client)
        except RedisError:
            logger.warning("Redis unavailable; queue and cache metrics skipped for this scrape")
            return

        yield GaugeMetricFamily("adaptiverag_queue_depth", "Jobs waiting in the queue", value=depth)

        cache = CounterMetricFamily(
            "adaptiverag_cache_requests",
            "Cache lookups, by cache and outcome",
            labels=["cache", "outcome"],
        )
        for name, outcomes in stats.items():
            for outcome, count in outcomes.items():
                cache.add_metric([name, outcome], count)
        yield cache


def render_metrics(client) -> bytes:
    """This process's own metrics, followed by the shared state read from Redis."""
    state = CollectorRegistry()
    state.register(RedisStateCollector(client))
    return generate_latest(REGISTRY) + generate_latest(state)


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST




def start_worker_metrics_server(port: int) -> bool:
    """
    Let a worker serve its own numbers (job timings, tokens) on its own port.
    A worker has no web server of its own, so it starts a small one here. If the port
    is taken, only this worker's metrics are lost; the worker keeps running.
    """
    try:
        start_http_server(port)
    except OSError:
        logger.warning("Could not serve worker metrics on port %s; continuing without them", port)
        return False
    logger.info("Worker metrics on port %s (/metrics)", port)
    return True