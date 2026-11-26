# backend/tests/unit/test_scaling_retries.py

from types import SimpleNamespace

import httpx
import pytest

from app.scaling import retries
from app.scaling.retries import (
    RetryingEmbeddings,
    backoff_delay,
    build_search_retry_policy,
    call_with_retry,
    is_transient_error,
)


class ApiError(Exception):
    """Looks like a Google/OpenAI API error: carries an HTTP status."""

    def __init__(self, code):
        super().__init__(f"status {code}")
        self.code = code


def _run(func, **overrides):
    sleeps = []
    options = dict(max_attempts=4, base_delay=1.0, max_delay=20.0, sleep=sleeps.append, rng=lambda: 1.0)
    options.update(overrides)
    return call_with_retry(func, **options), sleeps


# --- backoff_delay ---------------------------------------------------------

def test_backoff_ceiling_doubles_each_retry():
    delays = [backoff_delay(n, 1.0, 100.0, rng=lambda: 1.0) for n in range(4)]
    assert delays == [1.0, 2.0, 4.0, 8.0]


def test_backoff_is_capped_at_max_delay():
    assert backoff_delay(10, 1.0, 20.0, rng=lambda: 1.0) == 20.0


def test_backoff_is_random_between_zero_and_the_ceiling():
    assert backoff_delay(2, 1.0, 20.0, rng=lambda: 0.0) == 0.0
    assert backoff_delay(2, 1.0, 20.0, rng=lambda: 0.5) == 2.0
    samples = {backoff_delay(3, 1.0, 20.0) for _ in range(50)}
    assert len(samples) > 1 and all(0 <= s <= 8.0 for s in samples)


# --- is_transient_error ----------------------------------------------------

@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_transient_status_codes_are_retryable(code):
    assert is_transient_error(ApiError(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(code):
    assert not is_transient_error(ApiError(code))


def test_status_code_attribute_is_also_understood():
    error = Exception("boom")
    error.status_code = 503
    assert is_transient_error(error)


def test_timeouts_and_network_errors_are_retryable():
    assert is_transient_error(TimeoutError())
    assert is_transient_error(ConnectionError())
    assert is_transient_error(httpx.ConnectTimeout("slow"))
    assert is_transient_error(httpx.ConnectError("down"))


def test_ordinary_errors_are_not_retryable():
    assert not is_transient_error(ValueError("bad input"))


# --- call_with_retry -------------------------------------------------------

def test_success_on_the_first_try_does_not_sleep():
    result, sleeps = _run(lambda: "ok")
    assert (result, sleeps) == ("ok", [])


def test_retries_a_transient_error_then_succeeds():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ApiError(503)
        return "ok"

    result, sleeps = _run(flaky)

    assert result == "ok"
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]  # waits grow exponentially (rng pinned to the ceiling)


def test_gives_up_after_max_attempts_and_raises_the_last_error():
    attempts = []

    def always_down():
        attempts.append(1)
        raise ApiError(503)

    with pytest.raises(ApiError):
        _run(always_down, max_attempts=3)

    assert len(attempts) == 3


def test_a_non_transient_error_is_raised_at_once():
    attempts = []

    def bad_request():
        attempts.append(1)
        raise ApiError(400)

    with pytest.raises(ApiError):
        _run(bad_request)

    assert len(attempts) == 1


def test_one_attempt_means_no_retries():
    attempts = []

    def down():
        attempts.append(1)
        raise ApiError(503)

    with pytest.raises(ApiError):
        _run(down, max_attempts=1)

    assert len(attempts) == 1


# --- RetryingEmbeddings ----------------------------------------------------

class FlakyEmbeddings:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        if self.calls <= self.failures:
            raise ApiError(429)
        return [0.1, 0.2]

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def test_embeddings_retry_a_rate_limit(monkeypatch):
    monkeypatch.setattr(retries.time, "sleep", lambda s: None)
    inner = FlakyEmbeddings(failures=2)
    wrapped = RetryingEmbeddings(inner, max_attempts=4, base_delay=1.0, max_delay=20.0)

    assert wrapped.embed_query("hello") == [0.1, 0.2]
    assert inner.calls == 3


def test_embeddings_give_up_after_the_attempt_limit(monkeypatch):
    monkeypatch.setattr(retries.time, "sleep", lambda s: None)
    inner = FlakyEmbeddings(failures=99)
    wrapped = RetryingEmbeddings(inner, max_attempts=3, base_delay=1.0, max_delay=20.0)

    with pytest.raises(ApiError):
        wrapped.embed_query("hello")

    assert inner.calls == 3


# --- Azure Search policy ---------------------------------------------------

def _policy_settings(history_length, backoff=1.0, max_backoff=20.0):
    return {"history": [object()] * history_length, "backoff": backoff, "max_backoff": max_backoff}


def test_search_policy_limits_the_number_of_retries():
    policy = build_search_retry_policy(max_attempts=4, base_delay=1.0, max_delay=20.0)

    assert policy.total_retries == 3
    assert policy.connect_retries == 3 and policy.read_retries == 3 and policy.status_retries == 3


def test_search_policy_waits_a_random_time_below_the_exponential_ceiling():
    policy = build_search_retry_policy(max_attempts=4, base_delay=1.0, max_delay=20.0)
    settings = _policy_settings(history_length=4)  # ceiling = 1.0 * 2**3 = 8 s

    waits = {policy.get_backoff_time(settings) for _ in range(50)}

    assert len(waits) > 1
    assert all(0 <= w <= 8.0 for w in waits)


def test_search_policy_respects_the_max_delay():
    policy = build_search_retry_policy(max_attempts=10, base_delay=1.0, max_delay=5.0)

    waits = [policy.get_backoff_time(_policy_settings(history_length=8, max_backoff=5.0)) for _ in range(50)]

    assert max(waits) <= 5.0


def test_helpers_read_the_settings():
    settings = SimpleNamespace(
        scaling_retry_max_attempts=5,
        scaling_retry_base_delay_seconds=0.5,
        scaling_retry_max_delay_seconds=10.0,
    )

    embeddings = retries.retrying_embeddings(FlakyEmbeddings(0), settings)
    policy = retries.search_retry_policy(settings)

    assert embeddings.max_attempts == 5 and embeddings.base_delay == 0.5 and embeddings.max_delay == 10.0
    assert policy.total_retries == 4