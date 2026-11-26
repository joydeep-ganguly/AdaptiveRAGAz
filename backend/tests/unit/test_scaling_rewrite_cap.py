from types import SimpleNamespace

from langgraph.graph import END

from app.agent.nodes import rag_nodes


def _settings(monkeypatch, enabled, cap=1):
    monkeypatch.setattr(
        rag_nodes, "get_settings",
        lambda: SimpleNamespace(scaling_enabled=enabled, scaling_max_rewrites_per_request=cap),
    )


def test_scaling_on_stops_rewriting_at_the_configured_cap(monkeypatch):
    _settings(monkeypatch, enabled=True, cap=1)

    assert rag_nodes.should_generate({"retrieved_docs": "", "rewritting_count": 1}) == "transform_query"
    assert rag_nodes.should_generate({"retrieved_docs": "", "rewritting_count": 2}) == "generate"


def test_scaling_off_keeps_the_original_limit(monkeypatch):
    _settings(monkeypatch, enabled=False, cap=1)

    assert rag_nodes.should_generate({"retrieved_docs": "", "rewritting_count": 2}) == "transform_query"
    assert rag_nodes.should_generate({"retrieved_docs": "", "rewritting_count": 4}) == "generate"


def test_answer_check_ends_the_run_once_the_cap_is_passed(monkeypatch):
    _settings(monkeypatch, enabled=True, cap=1)
    state = {"messages": [SimpleNamespace(content="q"), SimpleNamespace(content="a")], "rewritting_count": 2}

    assert rag_nodes.check_answer_quality(state) == END