import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.nodes import rag_nodes


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage("Net sales were $574.8 billion [1].")


@pytest.fixture()
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(rag_nodes, "llm", llm)
    monkeypatch.setattr(rag_nodes, "write_debug_log", lambda *args, **kwargs: None)
    return llm


@pytest.mark.parametrize("documents", ["", "   \n  "])
def test_no_documents_gives_the_fixed_answer_without_calling_the_llm(fake_llm, documents):
    state = {"messages": [HumanMessage("What was Amazon's Q2 2024 revenue?")], "retrieved_docs": documents}

    result = rag_nodes.generate_node(state, config={})

    assert fake_llm.calls == 0
    assert result["messages"][0].content == rag_nodes.NO_DOCUMENTS_ANSWER
    assert "References" not in result["messages"][0].content


def test_missing_documents_key_is_treated_as_no_documents(fake_llm):
    result = rag_nodes.generate_node({"messages": [HumanMessage("q")]}, config={})

    assert fake_llm.calls == 0
    assert result["messages"][0].content == rag_nodes.NO_DOCUMENTS_ANSWER


def test_with_documents_the_llm_answers(fake_llm):
    state = {
        "messages": [HumanMessage("What were Amazon's net sales in 2023?")],
        "retrieved_docs": "--- Document 1 ---\ncompany_name: amazon\npage: 24\n\nContent:\nNet sales ...",
    }

    result = rag_nodes.generate_node(state, config={})

    assert fake_llm.calls == 1
    assert result["messages"][0].content == "Net sales were $574.8 billion [1]."
