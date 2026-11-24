# backend/app/scaling/rate_limit.py

"""
Per-user token-bucket rate limiter on Redis.

Each user has a bucket that holds up to `capacity` tokens and refills at
`refill_per_second`. A request costs one token. An empty bucket means the
request is refused, with the number of seconds until a token is available.

The refill-and-take step runs as ONE Lua script inside Redis, so it is atomic:
many API processes can check the same user at the same moment without ever
handing out more tokens than the bucket holds.
"""

from dataclasses import dataclass

RATE_LIMIT_KEY_PREFIX = "adaptiverag:ratelimit:"

# KEYS[1] = bucket key
# ARGV    = capacity, refill per second, cost of this request
# Returns {allowed (1 or 0), seconds to wait before retrying (as a string)}
_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

tokens = math.min(capacity, tokens + (now - ts) * refill)

local allowed = 0
local retry_after = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after = (cost - tokens) / refill
end

redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill) + 1)
return {allowed, tostring(retry_after)}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: float  # 0 when allowed


class RateLimiter:
    def __init__(self, client, capacity: int, refill_per_second: float):
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError("capacity must be >= 1 and refill_per_second must be > 0")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        # The client is passed in so tests can use a fake in-memory Redis.
        self._script = client.register_script(_TOKEN_BUCKET_LUA)

    def check(self, user_id: int) -> RateLimitResult:
        allowed, retry_after = self._script(
            keys=[f"{RATE_LIMIT_KEY_PREFIX}{user_id}"],
            args=[self.capacity, self.refill_per_second, 1],
        )
        return RateLimitResult(allowed=bool(int(allowed)), retry_after_seconds=float(retry_after))