# backend/tests/unit/test_scaling_cache.py

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.load import dumps
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, Generation
from redis.exceptions import ConnectionError as RedisConnectionError

from app.scaling.cache import (
    RedisEmbeddingCache,
    RedisLLMCache,
    cache_stats,
    normalise_prompt,
)


class CountingEmbeddings(Embeddings):
    """Stand-in for the real embedding model; counts how often it is really called."""

    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [[float(len(t)), 1.0] for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class BrokenRedis:
    """Every Redis call fails, like an outage."""

    def __getattr__(self, name):
        def _fail(*args, **kwargs):
            raise RedisConnectionError("redis is down")
        return _fail


@pytest.fixture()
def embedding_cache(fake_redis):
    inner = CountingEmbeddings()
    return RedisEmbeddingCache(inner, fake_redis, ttl_seconds=60, namespace="test-model"), inner


def _generation(text="the answer"):
    return [ChatGeneration(message=AIMessage(content=text))]


# ---- embeddings ----------------------------------------------------------

def test_repeated_embedding_comes_from_cache(embedding_cache, fake_redis):
    cache, inner = embedding_cache

    first = cache.embed_query("net sales 2023")
    second = cache.embed_query("net sales 2023")

    assert first == second
    assert inner.calls == 1
    assert cache_stats(fake_redis)["embedding"] == {"hit": 1, "miss": 1}


def test_embed_documents_only_computes_the_missing_texts(embedding_cache):
    cache, inner = embedding_cache
    cache.embed_query("already cached")

    vectors = cache.embed_documents(["already cached", "new text"])

    assert vectors == [[14.0, 1.0], [8.0, 1.0]]
    assert inner.calls == 2  # one call for the first query, one for just "new text"


def test_embedding_cache_entries_expire(embedding_cache, fake_redis):
    cache, _ = embedding_cache
    cache.embed_query("net sales 2023")

    key = cache._key("net sales 2023")
    assert 0 < fake_redis.ttl(key) <= 60


def test_embeddings_still_work_when_redis_is_down():
    inner = CountingEmbeddings()
    cache = RedisEmbeddingCache(inner, BrokenRedis(), ttl_seconds=60, namespace="test-model")

    assert cache.embed_query("hello") == [5.0, 1.0]
    assert cache.embed_query("hello") == [5.0, 1.0]
    assert inner.calls == 2  # no caching, but no failure either


# ---- LLM cache -----------------------------------------------------------

def test_llm_cache_round_trip(fake_redis):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    prompt = dumps([HumanMessage(content="What is 2 + 2?")])

    assert cache.lookup(prompt, "model-a") is None
    cache.update(prompt, "model-a", _generation("4"))
    hit = cache.lookup(prompt, "model-a")

    assert hit[0].message.content == "4"
    assert cache_stats(fake_redis)["llm"] == {"hit": 1, "miss": 1}


def test_llm_cache_ignores_message_ids(fake_redis):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    first_run = dumps([HumanMessage(content="What is 2 + 2?", id="run-1")])
    second_run = dumps([HumanMessage(content="What is 2 + 2?", id="run-2")])
    assert first_run != second_run  # the raw prompts differ only by id

    cache.update(first_run, "model-a", _generation("4"))

    assert cache.lookup(second_run, "model-a") is not None


def test_llm_cache_does_not_mix_different_prompts_or_models(fake_redis):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    prompt = dumps([HumanMessage(content="What is 2 + 2?")])
    other_prompt = dumps([HumanMessage(content="What is 3 + 3?")])
    cache.update(prompt, "model-a", _generation("4"))

    assert cache.lookup(other_prompt, "model-a") is None  # different question
    assert cache.lookup(prompt, "model-b") is None  # different model settings


def test_llm_cache_misses_when_the_conversation_history_differs(fake_redis):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    alone = dumps([HumanMessage(content="And for 2022?")])
    with_history = dumps([
        HumanMessage(content="What were net sales in 2023?"),
        AIMessage(content="It was X."),
        HumanMessage(content="And for 2022?"),
    ])
    cache.update(alone, "model-a", _generation("answer without context"))

    assert cache.lookup(with_history, "model-a") is None


def test_llm_cache_survives_a_redis_outage():
    cache = RedisLLMCache(BrokenRedis(), ttl_seconds=60)
    prompt = dumps([HumanMessage(content="hello")])

    assert cache.lookup(prompt, "model-a") is None  # behaves like a miss
    cache.update(prompt, "model-a", _generation())  # must not raise


def test_llm_cache_ignores_a_corrupt_entry(fake_redis):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    prompt = dumps([HumanMessage(content="hello")])
    fake_redis.set(cache._key(prompt, "model-a"), "not valid json")

    assert cache.lookup(prompt, "model-a") is None


def test_normalise_prompt_removes_ids_but_keeps_content():
    a = dumps([HumanMessage(content="hello", id="one")])
    b = dumps([HumanMessage(content="hello", id="two")])
    c = dumps([HumanMessage(content="hello there", id="one")])

    assert normalise_prompt(a) == normalise_prompt(b)
    assert normalise_prompt(a) != normalise_prompt(c)
    assert normalise_prompt("plain text prompt") == "plain text prompt"


def test_llm_cache_keeps_tool_calls_in_a_cached_response(fake_redis):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    prompt = dumps([HumanMessage(content="Find Amazon sales")])
    message = AIMessage(
        content="",
        tool_calls=[{"name": "retriever_docs", "args": {"query": "Amazon sales"}, "id": "call-1"}],
    )
    cache.update(prompt, "model-a", [ChatGeneration(message=message)])

    hit = cache.lookup(prompt, "model-a")

    assert hit[0].message.tool_calls[0]["name"] == "retriever_docs"
    assert hit[0].message.tool_calls[0]["args"] == {"query": "Amazon sales"}


def test_llm_cache_handles_plain_text_generations(fake_redis):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    cache.update("a plain prompt", "model-a", [Generation(text="plain answer")])

    assert cache.lookup("a plain prompt", "model-a")[0].text == "plain answer"


def test_cache_works_end_to_end_with_a_chat_model(fake_redis):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    model = FakeListChatModel(responses=["first answer", "second answer"], cache=cache)

    first = model.invoke([HumanMessage(content="What is 2 + 2?", id="run-1")])
    repeat = model.invoke([HumanMessage(content="What is 2 + 2?", id="run-2")])  # same question, new id
    different = model.invoke([HumanMessage(content="What is 3 + 3?")])

    assert first.content == "first answer"
    assert repeat.content == "first answer"  # served from the cache, the model was not called
    assert different.content == "second answer"  # a different question still reaches the model
    assert cache_stats(fake_redis)["llm"] == {"hit": 1, "miss": 2}


def test_install_llm_cache_sets_the_global_cache(fake_redis):
    from langchain_core.globals import get_llm_cache, set_llm_cache

    from app.scaling.cache import install_llm_cache

    try:
        install_llm_cache(fake_redis, ttl_seconds=60)
        assert isinstance(get_llm_cache(), RedisLLMCache)
    finally:
        set_llm_cache(None)  # never leave a global cache behind for other tests