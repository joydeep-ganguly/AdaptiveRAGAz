# backend/app/scaling/redis_client.py

"""One shared Redis connection for the scaling layer. Only imported when scaling is enabled."""

from functools import lru_cache

import redis

from app.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    # decode_responses=True makes Redis return normal strings instead of raw bytes.
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)