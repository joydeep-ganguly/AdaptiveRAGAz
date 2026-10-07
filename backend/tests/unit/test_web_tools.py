from types import SimpleNamespace

import pytest
from ddgs.exceptions import DDGSException

from app.agent.tools import web_tools


def _fake_ddgs(monkeypatch, results=None, error=None):
    def text(**kwargs):
        if error:
            raise error
        return results

    monkeypatch.setattr(web_tools, "DDGS", lambda: SimpleNamespace(text=text))


def test_ddgs_exception_becomes_a_no_results_message(monkeypatch):
    _fake_ddgs(monkeypatch, error=DDGSException("No results found."))

    answer = web_tools.web_search.invoke({"query": "anything"})

    assert "No results found for 'anything'" in answer
    assert "Do not search again" in answer


def test_empty_results_become_a_no_results_message(monkeypatch):
    _fake_ddgs(monkeypatch, results=[])

    answer = web_tools.web_search.invoke({"query": "anything"})

    assert "No results found for 'anything'" in answer


def test_results_are_formatted_with_title_body_and_url(monkeypatch):
    _fake_ddgs(monkeypatch, results=[{"title": "T", "href": "https://a.example", "body": "B"}])

    answer = web_tools.web_search.invoke({"query": "q"})

    assert "1. **T**" in answer
    assert "B" in answer
    assert "https://a.example" in answer