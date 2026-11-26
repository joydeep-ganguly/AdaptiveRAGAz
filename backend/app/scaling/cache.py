# backend/app/scaling/cache.py

"""
Redis caches for the scale profile: query/document embeddings and LLM responses.

Both caches are correct by construction and fail open:
  * An embedding is cached under the exact text it was made from.
  * An LLM response is cached under the FULL prompt (the whole conversation so
    far plus the model settings), so a hit only happens when the model would
    have received exactly the same input. Message and tool-call ids are removed
    from the key because they are different on every run.
  * If Redis is unavailable the cache steps are skipped and the real call is
    made, so a cache problem never breaks a question.

Hit and miss counts are kept in Redis (see cache_stats) for the load-test results.

Note: this caches single calls, not whole answers. Skipping the agent run for a
repeated question would leave the conversation memory (LangGraph checkpoint)
without that turn and break follow-up questions.
"""

import hashlib
import json
import logging

from langchain_core.caches import BaseCache
from langchain_core.embeddings import Embeddings
from langchain_core.globals import set_llm_cache
from langchain_core.load import dumps, loads
from langchain_core.outputs import ChatGeneration, Generation
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "adaptiverag:cache:"
STATS_KEY_PREFIX = "adaptiverag:cachestats:"

# Keys that differ on every run without changing the meaning of a prompt.
_VOLATILE_KEYS = ("id", "tool_call_id")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_volatile(value):
    """Remove message ids and tool-call ids (string values only) from a parsed prompt."""
    if isinstance(value, dict):
        return {
            k: _strip_volatile(v)
            for k, v in value.items()
            if not (k in _VOLATILE_KEYS and isinstance(v, str))
        }
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def normalise_prompt(prompt: str) -> str:
    """Prompts for chat models are JSON; anything else is used as is."""
    try:
        parsed = json.loads(prompt)
    except (TypeError, ValueError):
        return prompt
    return json.dumps(_strip_volatile(parsed), sort_keys=True)


def _load_message(text: str):
    # Newer langchain-core wants to be told which classes it may rebuild; older
    # versions do not accept the argument.
    try:
        return loads(text, allowed_objects="messages")
    except TypeError:
        return loads(text)


def _generation_to_json(generation) -> dict:
    if isinstance(generation, ChatGeneration):
        return {"kind": "chat", "message": dumps(generation.message), "info": generation.generation_info}
    return {"kind": "text", "text": generation.text, "info": generation.generation_info}


def _generation_from_json(data: dict):
    if data["kind"] == "chat":
        message = _load_message(data["message"])
        # A cached answer costs no tokens now, so the original usage must not be
        # counted again by the token budgets.
        if getattr(message, "usage_metadata", None):
            message.usage_metadata = None
        return ChatGeneration(message=message, generation_info=data.get("info"))
    return Generation(text=data["text"], generation_info=data.get("info"))


def _count(client, name: str, outcome: str) -> None:
    try:
        client.incr(f"{STATS_KEY_PREFIX}{name}:{outcome}")
    except RedisError:
        pass


def cache_stats(client) -> dict:
    """{'llm': {'hit': n, 'miss': n}, 'embedding': {...}} — handy for the load-test results."""
    stats = {}
    for name in ("llm", "embedding"):
        stats[name] = {
            outcome: int(client.get(f"{STATS_KEY_PREFIX}{name}:{outcome}") or 0)
            for outcome in ("hit", "miss")
        }
    return stats


class RedisEmbeddingCache(Embeddings):
    """Wraps any LangChain embeddings object and remembers its results in Redis."""

    def __init__(self, inner: Embeddings, client, ttl_seconds: int, namespace: str):
        self.inner = inner
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.namespace = namespace

    def _key(self, text: str) -> str:
        return f"{CACHE_KEY_PREFIX}emb:{_sha256(self.namespace + chr(10) + text)}"

    def _get_many(self, texts: list[str]) -> list[list[float] | None]:
        try:
            values = self.client.mget([self._key(t) for t in texts])
        except RedisError:
            logger.warning("Embedding cache unavailable; computing embeddings directly")
            return [None] * len(texts)
        return [json.loads(v) if v is not None else None for v in values]

    def _put_many(self, texts: list[str], vectors: list[list[float]]) -> None:
        try:
            pipe = self.client.pipeline()
            for text, vector in zip(texts, vectors):
                pipe.set(self._key(text), json.dumps(vector), ex=self.ttl_seconds)
            pipe.execute()
        except RedisError:
            logger.warning("Embedding cache unavailable; results not stored")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        cached = self._get_many(texts)
        missing = [i for i, v in enumerate(cached) if v is None]
        for _ in range(len(texts) - len(missing)):
            _count(self.client, "embedding", "hit")
        if missing:
            for _ in missing:
                _count(self.client, "embedding", "miss")
            fresh = self.inner.embed_documents([texts[i] for i in missing])
            self._put_many([texts[i] for i in missing], fresh)
            for index, vector in zip(missing, fresh):
                cached[index] = vector
        return cached

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class RedisLLMCache(BaseCache):
    """LangChain LLM cache backed by Redis (see the module docstring for why it is safe)."""

    def __init__(self, client, ttl_seconds: int):
        self.client = client
        self.ttl_seconds = ttl_seconds

    def _key(self, prompt: str, llm_string: str) -> str:
        return f"{CACHE_KEY_PREFIX}llm:{_sha256(normalise_prompt(prompt) + chr(10) + llm_string)}"

    def lookup(self, prompt: str, llm_string: str):
        try:
            value = self.client.get(self._key(prompt, llm_string))
        except RedisError:
            logger.warning("LLM cache unavailable; calling the model directly")
            return None
        if value is None:
            _count(self.client, "llm", "miss")
            return None
        try:
            generations = [_generation_from_json(item) for item in json.loads(value)]
        except Exception:
            logger.warning("Ignoring an unreadable LLM cache entry")
            _count(self.client, "llm", "miss")
            return None
        _count(self.client, "llm", "hit")
        return generations

    def update(self, prompt: str, llm_string: str, return_val) -> None:
        try:
            payload = json.dumps([_generation_to_json(generation) for generation in return_val])
            self.client.set(self._key(prompt, llm_string), payload, ex=self.ttl_seconds)
        except (RedisError, TypeError, ValueError):
            logger.warning("LLM cache unavailable; response not stored")

    def clear(self, **kwargs) -> None:
        for key in self.client.scan_iter(f"{CACHE_KEY_PREFIX}llm:*"):
            self.client.delete(key)


def cached_embeddings(inner: Embeddings) -> Embeddings:
    """Used by app/agent/llm.py when the scale profile is on."""
    from app.config import get_settings
    from app.scaling.redis_client import get_redis

    return RedisEmbeddingCache(
        inner,
        get_redis(),
        ttl_seconds=get_settings().scaling_cache_ttl_seconds,
        namespace=str(getattr(inner, "model", type(inner).__name__)),
    )


def install_llm_cache(client, ttl_seconds: int) -> None:
    """Turn the LLM cache on for every model in this process (call once, at worker start)."""
    set_llm_cache(RedisLLMCache(client, ttl_seconds))