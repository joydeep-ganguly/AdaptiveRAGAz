from types import SimpleNamespace

import pytest
from azure.core.exceptions import HttpResponseError

from app.agent.retrieval import azure_search_store as store


class FakeSearchClient:
    """Records the search() call and returns canned hits."""

    def __init__(self, hits=None, error=None):
        self.hits = hits or []
        self.error = error
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return iter(self.hits)


@pytest.fixture()
def fake_azure(monkeypatch):
    """Fake settings, embeddings and search client; returns the client and settings."""
    settings = SimpleNamespace(
        azure_search_endpoint="https://example.search.windows.net",
        azure_search_index_name="idx-test",
        azure_search_keyword_mode="hard",
        azure_search_exhaustive=False,
    )
    client = FakeSearchClient()
    embeddings = SimpleNamespace(embed_query=lambda text: [0.1, 0.2, 0.3])
    monkeypatch.setattr(store, "get_settings", lambda: settings)
    monkeypatch.setattr(store, "get_embeddings", lambda: embeddings)
    monkeypatch.setattr(store, "get_search_client", lambda: client)
    return SimpleNamespace(client=client, settings=settings)


# ---------------------------------------------------------------- filters

def test_metadata_filter_single():
    assert store.build_metadata_filter({"company_name": "amazon"}) == "company_name eq 'amazon'"


def test_metadata_filter_multiple_with_int_year():
    f = {"company_name": "amazon", "doc_type": "10-k", "fiscal_year": 2023, "fiscal_quarter": "q1"}
    assert store.build_metadata_filter(f) == (
        "company_name eq 'amazon' and doc_type eq '10-k' and fiscal_year eq 2023 and fiscal_quarter eq 'q1'"
    )


def test_metadata_filter_year_string_is_written_as_number():
    assert store.build_metadata_filter({"fiscal_year": "2023"}) == "fiscal_year eq 2023"


def test_metadata_filter_escapes_quotes():
    assert store.build_metadata_filter({"company_name": "o'brien"}) == "company_name eq 'o''brien'"


def test_metadata_filter_empty():
    assert store.build_metadata_filter({}) is None
    assert store.build_metadata_filter(None) is None


def test_metadata_filter_rejects_unknown_field():
    with pytest.raises(ValueError):
        store.build_metadata_filter({"page": 3})


def test_metadata_filter_rejects_non_numeric_year():
    with pytest.raises(ValueError):
        store.build_metadata_filter({"fiscal_year": "2023 or true"})


# ---------------------------------------------------------------- keywords

def test_keyword_query_quotes_phrases_and_ors():
    assert store.build_keyword_query(["net sales", "operating income"]) == '"net sales" | "operating income"'


def test_keyword_query_single_word():
    assert store.build_keyword_query(["revenue"]) == '"revenue"'


def test_keyword_query_removes_quotes_and_backslashes():
    assert store.build_keyword_query(['net "sales"', "a\\b"]) == '"net sales" | "a b"'


def test_keyword_query_drops_duplicates_and_empties():
    assert store.build_keyword_query(["Net Sales", "net sales", "", "  ", "---"]) == '"Net Sales"'


def test_keyword_query_none_when_nothing_usable():
    assert store.build_keyword_query([]) is None
    assert store.build_keyword_query(None) is None
    assert store.build_keyword_query(['""', "-"]) is None


def test_filter_hard_mode_adds_ismatch():
    out = store.build_filter({"company_name": "amazon"}, ["net sales", "income"], "hard")
    assert out == (
        "company_name eq 'amazon' and "
        "search.ismatch('\"net sales\" | \"income\"', 'page_text', 'simple', 'any')"
    )


def test_filter_hard_mode_without_metadata():
    out = store.build_filter({}, ["income"], "hard")
    assert out == "search.ismatch('\"income\"', 'page_text', 'simple', 'any')"


def test_filter_hard_mode_doubles_apostrophes_in_keywords():
    out = store.build_filter({}, ["company's growth"], "hard")
    assert "''" in out and "company''s growth" in out


def test_filter_hybrid_mode_has_no_ismatch():
    assert store.build_filter({"company_name": "amazon"}, ["income"], "hybrid") == "company_name eq 'amazon'"
    assert store.build_filter({}, ["income"], "hybrid") is None


def test_filter_hard_mode_without_keywords():
    assert store.build_filter({"doc_type": "10-k"}, [], "hard") == "doc_type eq '10-k'"


# ---------------------------------------------------------------- search call

def test_search_request_shape(fake_azure):
    store.search_docs_azure(
        "cash flow", {"company_name": "amazon", "fiscal_year": 2023}, ["cash flow", "liquidity"], k=50
    )
    call = fake_azure.client.calls[0]
    assert call["top"] == 50
    assert call["search_text"] == '"cash flow" | "liquidity"'
    assert call["filter"] == (
        "company_name eq 'amazon' and fiscal_year eq 2023 and "
        "search.ismatch('\"cash flow\" | \"liquidity\"', 'page_text', 'simple', 'any')"
    )
    assert call["select"] == [
        "page_text", "company_name", "doc_type", "fiscal_year", "fiscal_quarter", "page", "source_file"
    ]
    assert call["vector_filter_mode"] == "preFilter"
    assert len(call["vector_queries"]) == 1
    vq = call["vector_queries"][0]
    assert vq.vector == [0.1, 0.2, 0.3]
    assert vq.k_nearest_neighbors == 50
    assert vq.fields == "vector"
    assert not vq.exhaustive


def test_search_hybrid_mode_keeps_keywords_out_of_filter(fake_azure):
    fake_azure.settings.azure_search_keyword_mode = "hybrid"
    store.search_docs_azure("q", {"company_name": "amazon"}, ["income"], k=10)
    call = fake_azure.client.calls[0]
    assert call["filter"] == "company_name eq 'amazon'"
    assert call["search_text"] == '"income"'


def test_search_exhaustive_flag(fake_azure):
    fake_azure.settings.azure_search_exhaustive = True
    store.search_docs_azure("q", {}, [], k=5)
    assert fake_azure.client.calls[0]["vector_queries"][0].exhaustive is True


def test_search_without_filters_or_keywords(fake_azure):
    store.search_docs_azure("q", None, None, k=5)
    call = fake_azure.client.calls[0]
    assert call["filter"] is None
    assert call["search_text"] is None


# ---------------------------------------------------------------- results

def test_results_become_documents_without_none_values(fake_azure):
    fake_azure.client.hits = [
        {
            "@search.score": 0.9,
            "page_text": "# Cash flows\n\ntext",
            "company_name": "amazon",
            "doc_type": "10-k",
            "fiscal_year": 2023,
            "fiscal_quarter": None,
            "page": 51,
            "source_file": "amazon 10-k 2023.pdf",
        },
        {
            "page_text": "other",
            "company_name": "apple",
            "doc_type": "8-k",
            "fiscal_year": 2023,
            "fiscal_quarter": "q4",
            "page": 2,
            "source_file": "apple 8-k q4 2023.pdf",
        },
    ]
    docs = store.search_docs_azure("q", {}, [], k=10)
    assert [d.page_content for d in docs] == ["# Cash flows\n\ntext", "other"]
    assert list(docs[0].metadata) == ["company_name", "doc_type", "fiscal_year", "page", "source_file"]
    assert docs[0].metadata["fiscal_year"] == 2023
    assert "fiscal_quarter" not in docs[0].metadata
    assert docs[1].metadata["fiscal_quarter"] == "q4"
    assert "@search.score" not in docs[0].metadata


def test_no_hits_gives_empty_list(fake_azure):
    assert store.search_docs_azure("q", {}, [], k=10) == []


# ---------------------------------------------------------------- errors

def test_azure_error_is_wrapped(fake_azure):
    fake_azure.client.error = HttpResponseError(message="boom")
    with pytest.raises(store.AzureSearchError):
        store.search_docs_azure("q", {}, [], k=10)


def test_missing_endpoint_raises_config_error(monkeypatch):
    settings = SimpleNamespace(azure_search_endpoint=None, azure_search_index_name="i")
    monkeypatch.setattr(store, "get_settings", lambda: settings)
    store.get_search_client.cache_clear()
    try:
        with pytest.raises(store.AzureSearchConfigError):
            store.get_search_client()
    finally:
        store.get_search_client.cache_clear()
