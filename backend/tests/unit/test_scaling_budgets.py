# backend/tests/unit/test_scaling_budgets.py

import operator
from typing import Annotated, TypedDict

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, START, StateGraph

from app.scaling.budgets import (
    BudgetedGraph,
    BudgetExceeded,
    TokenBudgetCallback,
    UserTokenLedger,
)
from app.scaling.cache import RedisLLMCache


class UsageChatModel(BaseChatModel):
    """A fake model that reports a fixed number of tokens for every call."""

    tokens_per_call: int = 100
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "usage-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        usage = {
            "input_tokens": self.tokens_per_call - 10,
            "output_tokens": 10,
            "total_tokens": self.tokens_per_call,
        }
        message = AIMessage(content="ok", usage_metadata=usage)
        return ChatResult(generations=[ChatGeneration(message=message)])


class _State(TypedDict):
    messages: Annotated[list, operator.add]
    rounds: int


def _looping_graph(model, rounds_wanted):
    """A graph whose node calls the model WITHOUT passing config (like your RAG nodes)
    and loops `rounds_wanted` times."""

    def node(state):
        reply = model.invoke(state["messages"])  # no config passed on purpose
        return {"messages": [reply], "rounds": state.get("rounds", 0) + 1}

    def again(state):
        return "node" if state["rounds"] < rounds_wanted else END

    builder = StateGraph(_State)
    builder.add_node("node", node)
    builder.add_edge(START, "node")
    builder.add_conditional_edges("node", again, ["node", END])
    return builder.compile()


@pytest.fixture()
def ledger(fake_redis):
    return UserTokenLedger(fake_redis, daily_limit=1000)


def _run(graph_model, ledger, rounds, max_tokens=10_000, max_calls=20, user_id=1):
    callback = TokenBudgetCallback(ledger, user_id=user_id, max_tokens=max_tokens, max_llm_calls=max_calls)
    graph = BudgetedGraph(_looping_graph(graph_model, rounds), callback)
    result = graph.invoke({"messages": [HumanMessage(content="hi")], "rounds": 0})
    return callback, result


# ---- counting ------------------------------------------------------------

def test_tokens_and_calls_are_counted_across_a_graph_run(ledger):
    callback, _ = _run(UsageChatModel(tokens_per_call=100), ledger, rounds=3)

    assert callback.usage == {"tokens": 300, "llm_calls": 3}
    assert ledger.used_today(1) == 300


def test_budgeted_graph_passes_get_state_through(ledger):
    callback = TokenBudgetCallback(ledger, 1, 1000, 10)
    inner = _looping_graph(UsageChatModel(), 1)
    inner.get_state = lambda config=None: "state-object"

    assert BudgetedGraph(inner, callback).get_state({"x": 1}) == "state-object"


# ---- limits --------------------------------------------------------------

def test_runaway_loop_is_stopped_by_the_call_limit(ledger):
    model = UsageChatModel(tokens_per_call=1)

    with pytest.raises(BudgetExceeded) as stopped:
        _run(model, ledger, rounds=1000, max_calls=5)

    assert stopped.value.reason == "llm_calls"
    assert model.calls == 5  # the sixth call never happened


def test_request_token_limit_stops_the_run(ledger):
    model = UsageChatModel(tokens_per_call=100)

    with pytest.raises(BudgetExceeded) as stopped:
        _run(model, ledger, rounds=50, max_tokens=250)

    assert stopped.value.reason == "request_tokens"
    assert model.calls == 3  # 100 + 100 + 100 crosses 250; no fourth call


def test_daily_user_limit_stops_the_run(fake_redis):
    ledger = UserTokenLedger(fake_redis, daily_limit=250)
    model = UsageChatModel(tokens_per_call=100)

    with pytest.raises(BudgetExceeded) as stopped:
        _run(model, ledger, rounds=50)

    assert stopped.value.reason == "daily_tokens"
    assert ledger.used_today(1) == 300


def test_one_users_usage_does_not_affect_another(ledger):
    ledger.add(user_id=1, tokens=5000)  # user 1 is over the daily limit

    callback, _ = _run(UsageChatModel(), ledger, rounds=1, user_id=2)

    assert callback.usage["llm_calls"] == 1


def test_budget_messages_are_readable():
    assert "daily" in BudgetExceeded("daily_tokens").user_message.lower()
    assert BudgetExceeded("something-new").user_message  # unknown reasons still give a message


# ---- cache interaction ---------------------------------------------------

def test_cached_answers_cost_no_tokens(fake_redis, ledger):
    cache = RedisLLMCache(fake_redis, ttl_seconds=60)
    model = UsageChatModel(tokens_per_call=100, cache=cache)

    first, _ = _run(model, ledger, rounds=1)   # real call: 100 tokens
    second, _ = _run(model, ledger, rounds=1)  # served from the cache

    assert first.usage == {"tokens": 100, "llm_calls": 1}
    assert second.usage["tokens"] == 0
    assert model.calls == 1
    assert ledger.used_today(1) == 100


# ---- ledger --------------------------------------------------------------

def test_ledger_counts_per_user_and_expires(ledger, fake_redis):
    ledger.add(1, 40)
    ledger.add(1, 60)
    ledger.add(2, 7)

    assert ledger.used_today(1) == 100
    assert ledger.used_today(2) == 7
    assert not ledger.is_exhausted(1)
    ledger.add(1, 900)
    assert ledger.is_exhausted(1)
    assert 0 < fake_redis.ttl(ledger._key(1)) <= 2 * 24 * 3600


def test_seconds_until_reset_is_within_a_day():
    assert 1 <= UserTokenLedger.seconds_until_reset() <= 24 * 3600