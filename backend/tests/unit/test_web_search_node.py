from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.nodes import web_search_node as node
from app.agent.tools import foundry_web_search as fws
from app.agent.tools.web_tools import web_search

QUERY = "What are the top technology news headlines today?"
STATE = {"messages": [HumanMessage(QUERY)]}


@pytest.fixture()
def settings(monkeypatch):
    fake = SimpleNamespace(web_search_fallback_enabled=True)
    monkeypatch.setattr(node, "get_settings", lambda: fake)
    return fake


@pytest.fixture()
def foundry(monkeypatch):
    """Fake surface of the Foundry module: configured by default, records the queries it receives."""
    fake = SimpleNamespace(
        configured=True,
        result=fws.WebSearchResult(text="Answer.", sources=()),
        error=None,
        queries=[],
    )

    def fake_search(query):
        fake.queries.append(query)
        if fake.error:
            raise fake.error
        return fake.result

    monkeypatch.setattr(fws, "is_configured", lambda: fake.configured)
    monkeypatch.setattr(fws, "search", fake_search)
    return fake


@pytest.fixture()
def duckduckgo(monkeypatch):
    fake = SimpleNamespace(calls=[], messages=[AIMessage(content="from duckduckgo")])

    def fake_answer(query):
        fake.calls.append(query)
        return fake.messages

    monkeypatch.setattr(node, "_duckduckgo_answer", fake_answer)
    return fake


# ------------------------------------------------------------------ Foundry path
def test_foundry_success_returns_one_ai_message_with_sources(settings, foundry, duckduckgo):
    foundry.result = fws.WebSearchResult(
        text="Today's headlines.",
        sources=(fws.WebSearchSource("First", "https://a.example/1"),),
    )

    result = node.web_search_node(STATE)

    assert len(result["messages"]) == 1
    message = result["messages"][0]
    assert isinstance(message, AIMessage)
    assert message.content == "Today's headlines.\n\n**Sources**\n\n1. [First](https://a.example/1)"
    assert foundry.queries == [QUERY]
    assert duckduckgo.calls == []


def test_foundry_success_without_sources_returns_just_the_answer(settings, foundry, duckduckgo):
    result = node.web_search_node(STATE)

    assert result["messages"][0].content == "Answer."

# ------------------------------------------- DuckDuckGo failure never crashes the graph
def _boom(query):
    raise RuntimeError("No results found.")


def test_duckduckgo_failure_returns_unavailable_message(settings, foundry, monkeypatch):
    foundry.configured = False
    monkeypatch.setattr(node, "_duckduckgo_answer", _boom)

    result = node.web_search_node(STATE)

    assert [m.content for m in result["messages"]] == [node.UNAVAILABLE_MESSAGE]


def test_foundry_and_duckduckgo_both_failing_returns_unavailable_message(settings, foundry, monkeypatch):
    foundry.error = fws.FoundryWebSearchError("timed out")
    monkeypatch.setattr(node, "_duckduckgo_answer", _boom)

    result = node.web_search_node(STATE)

    assert [m.content for m in result["messages"]] == [node.UNAVAILABLE_MESSAGE]


# --------------------------------------------------------------------- fallback
def test_foundry_failure_with_fallback_uses_duckduckgo(settings, foundry, duckduckgo):
    foundry.error = fws.FoundryWebSearchError("timed out")

    result = node.web_search_node(STATE)

    assert result["messages"] is duckduckgo.messages
    assert duckduckgo.calls == [QUERY]
    assert foundry.queries == [QUERY]


def test_foundry_failure_without_fallback_returns_unavailable_message(settings, foundry, duckduckgo):
    settings.web_search_fallback_enabled = False
    foundry.error = fws.FoundryWebSearchError("timed out")

    result = node.web_search_node(STATE)

    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == node.UNAVAILABLE_MESSAGE
    assert duckduckgo.calls == []


def test_not_configured_goes_straight_to_duckduckgo(settings, foundry, duckduckgo):
    foundry.configured = False

    result = node.web_search_node(STATE)

    assert result["messages"] is duckduckgo.messages
    assert duckduckgo.calls == [QUERY]
    assert foundry.queries == []


def test_not_configured_uses_duckduckgo_even_when_fallback_is_disabled(settings, foundry, duckduckgo):
    settings.web_search_fallback_enabled = False
    foundry.configured = False

    result = node.web_search_node(STATE)

    assert result["messages"] is duckduckgo.messages
    assert foundry.queries == []


# -------------------------------------------------------- DuckDuckGo helper itself
def test_duckduckgo_helper_runs_the_original_agent_unchanged(monkeypatch):
    captured = {}

    class _FakeAgent:
        def invoke(self, payload):
            captured["payload"] = payload
            return {"messages": ["trace"]}

    def _fake_create_agent(**kwargs):
        captured["kwargs"] = kwargs
        return _FakeAgent()

    monkeypatch.setattr(node, "create_agent", _fake_create_agent)

    messages = node._duckduckgo_answer(QUERY)

    assert messages == ["trace"]
    assert captured["kwargs"]["tools"] == [web_search]
    assert captured["kwargs"]["system_prompt"] == node.FALLBACK_SYSTEM_PROMPT
    assert captured["payload"]["messages"][0].content == QUERY


def test_fallback_prompt_still_tells_the_agent_to_use_the_web_search_tool():
    assert "web_search" in node.FALLBACK_SYSTEM_PROMPT