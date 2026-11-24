# backend/tests/unit/test_scaling_rate_limit.py

import time

import pytest

from app.scaling.rate_limit import RATE_LIMIT_KEY_PREFIX, RateLimiter


def test_burst_is_allowed_then_refused(fake_redis):
    limiter = RateLimiter(fake_redis, capacity=3, refill_per_second=0.5)

    results = [limiter.check(user_id=1) for _ in range(4)]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert 0 < results[3].retry_after_seconds <= 2.0  # one token at 0.5/s takes at most 2 s


def test_each_user_has_their_own_bucket(fake_redis):
    limiter = RateLimiter(fake_redis, capacity=1, refill_per_second=0.5)

    assert limiter.check(user_id=1).allowed
    assert not limiter.check(user_id=1).allowed
    assert limiter.check(user_id=2).allowed  # user 2 is unaffected by user 1


def test_tokens_refill_over_time(fake_redis):
    limiter = RateLimiter(fake_redis, capacity=1, refill_per_second=100)

    assert limiter.check(user_id=1).allowed
    time.sleep(0.1)  # at 100 tokens/s the bucket is full again almost at once
    assert limiter.check(user_id=1).allowed


def test_bucket_expires_so_idle_users_cost_nothing(fake_redis):
    limiter = RateLimiter(fake_redis, capacity=2, refill_per_second=1)

    limiter.check(user_id=1)

    assert 0 < fake_redis.ttl(f"{RATE_LIMIT_KEY_PREFIX}1") <= 3


def test_invalid_settings_are_rejected(fake_redis):
    with pytest.raises(ValueError):
        RateLimiter(fake_redis, capacity=0, refill_per_second=1)
    with pytest.raises(ValueError):
        RateLimiter(fake_redis, capacity=5, refill_per_second=0)