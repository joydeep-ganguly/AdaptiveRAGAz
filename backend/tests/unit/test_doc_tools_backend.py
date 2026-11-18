from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from app.agent.tools import doc_tools


def _doc(page):
    return Document(
        page_content=f"# Heading {page}\n\ntext {page}",
        metadata={"company_name": "amazon", "doc_type": "10-k", "fiscal_year": 2023, "page": page},
    )


@pytest.fixture()
def wired(monkeypatch):
    """Fake filters, keywords, both search backends and the re-rank; records the search calls."""
    calls = {"chroma": [], "azure": []}
    state = {"docs": [_doc(1), _doc(2)]}

    def fake_chroma(query, filters, keywords, k=None):
        calls["chroma"].append((query, filters, keywords, k))
        return state["docs"]

    def fake_azure(query, filters, keywords, k=None):
        calls["azure"].append((query, filters, keywords, k))
        return state["docs"]

    monkeypatch.setattr(doc_tools, "extract_filters", lambda q: {"company_name": "amazon"})
    monkeypatch.setattr(doc_tools, "generate_ranking_keywords", lambda q: ["revenue"])
    monkeypatch.setattr(doc_tools, "search_docs", fake_chroma)
    monkeypatch.setattr(doc_tools, "search_docs_azure", fake_azure)
    monkeypatch.setattr(doc_tools, "rank_documents_by_keywords", lambda docs, kw, k: docs[:k])

    def use(backend):
        monkeypatch.setattr(doc_tools, "get_settings", lambda: SimpleNamespace(retrieval_backend=backend))

    return SimpleNamespace(calls=calls, state=state, use=use)


def test_chroma_backend_searches_chroma(wired):
    wired.use("chroma")
    doc_tools.retriever_docs.invoke({"query": "amazon revenue", "k": 5})
    assert wired.calls["chroma"] == [("amazon revenue", {"company_name": "amazon"}, ["revenue"], 50)]
    assert wired.calls["azure"] == []


def test_azure_backend_searches_azure(wired):
    wired.use("azure")
    doc_tools.retriever_docs.invoke({"query": "amazon revenue", "k": 5})
    assert wired.calls["azure"] == [("amazon revenue", {"company_name": "amazon"}, ["revenue"], 50)]
    assert wired.calls["chroma"] == []


def test_candidate_count_follows_k(wired):
    wired.use("azure")
    doc_tools.retriever_docs.invoke({"query": "q", "k": 3})
    assert wired.calls["azure"][0][3] == 30


def test_output_is_identical_for_both_backends(wired):
    wired.use("chroma")
    from_chroma = doc_tools.retriever_docs.invoke({"query": "q", "k": 5})
    wired.use("azure")
    from_azure = doc_tools.retriever_docs.invoke({"query": "q", "k": 5})
    assert from_chroma == from_azure
    assert "--- Document 1 ---" in from_azure
    assert "company_name: amazon" in from_azure
    assert "page: 2" in from_azure
    assert "Content:\n# Heading 1" in from_azure


def test_no_results_message(wired):
    wired.use("azure")
    wired.state["docs"] = []
    out = doc_tools.retriever_docs.invoke({"query": "nothing here", "k": 5})
    assert "found for the query: 'nothing here'" in out
