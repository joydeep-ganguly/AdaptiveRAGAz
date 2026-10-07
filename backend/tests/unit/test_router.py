from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from app.agent.router import route_question


class _FakeStructuredLLM:
    def __init__(self, datasource):
        self._datasource = datasource

    def invoke(self, messages):
        return SimpleNamespace(datasource=self._datasource)


class _FakeLLM:
    def __init__(self, datasource):
        self._datasource = datasource

    def with_structured_output(self, schema):
        return _FakeStructuredLLM(self._datasource)


def _patch_llm(monkeypatch, datasource):
    # Patched where it's used (app.agent.router), not where it's defined —
    # get_llm() is @lru_cache'd, so patching the source module wouldn't help.
    monkeypatch.setattr("app.agent.router.get_llm", lambda: _FakeLLM(datasource))


def test_routes_financial_question_to_retrieve(monkeypatch):
    _patch_llm(monkeypatch, "retrieve")
    state = {"messages": [HumanMessage("What was Amazon's revenue in 2023?")]}
    assert route_question(state) == "retrieve"


def test_routes_employee_question_to_sql_agent(monkeypatch):
    _patch_llm(monkeypatch, "sql_agent")
    state = {"messages": [HumanMessage("Who is the CFO?")]}
    assert route_question(state) == "sql_agent"


def test_routes_general_question_to_web_search(monkeypatch):
    _patch_llm(monkeypatch, "web_search")
    state = {"messages": [HumanMessage("What is the capital of France?")]}
    assert route_question(state) == "web_search"


def test_routes_analysis_question_to_code_execution(monkeypatch):
    _patch_llm(monkeypatch, "code_execution")
    state = {"messages": [HumanMessage("Create a bar chart of ratings")]}
    assert route_question(state) == "code_execution"