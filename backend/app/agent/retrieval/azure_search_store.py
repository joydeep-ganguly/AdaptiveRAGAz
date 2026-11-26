"""
Azure AI Search retrieval for the RAG filings (Sub-Plan 4).

search_docs_azure() mirrors vectorstore.search_docs(): same arguments and the
same return type (a list of langchain Documents), so retriever_docs can swap
between the two with one setting (RETRIEVAL_BACKEND). The BM25 re-rank and the
tool output downstream do not change.

Differences from the Chroma path:
  * No MMR (Azure AI Search has none, and the vector field is not stored, so
    there is no client-side MMR either). The caller asks for the same number of
    candidates as before and the BM25 re-rank picks the final pages.
  * Keywords: Chroma's `$contains` was a hard "page must contain a keyword"
    filter. In "hard" mode (default) the same rule is added to the $filter with
    search.ismatch; in "hybrid" mode the keywords only add to the score. Matching
    is token based with stemming (en.lucene), not a raw substring match.
  * The page's `file_hash` no longer exists, so it is not in the metadata.

Auth is DefaultAzureCredential (az login locally, managed identity in Azure);
the identity needs the role "Search Index Data Reader" on the search service.
"""
import logging
from functools import lru_cache

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from langchain_core.documents import Document

from app.agent.llm import get_embeddings
from app.config import get_settings

logger = logging.getLogger(__name__)

VECTOR_FIELD = "vector"
TEXT_FIELD = "page_text"
# Order of the metadata keys in the returned Documents (None values are dropped).
METADATA_FIELDS = (
    "company_name",
    "doc_type",
    "fiscal_year",
    "fiscal_quarter",
    "page",
    "source_file",
)
_STRING_FILTERS = ("company_name", "doc_type", "fiscal_quarter")
_INT_FILTERS = ("fiscal_year",)


class AzureSearchError(Exception):
    """The Azure AI Search request failed (details are in the log)."""


class AzureSearchConfigError(AzureSearchError):
    """Azure AI Search is selected but not configured."""


@lru_cache(maxsize=1)
def get_search_client() -> SearchClient:
    """Lazily built and cached; never created at import time."""
    settings = get_settings()
    if not settings.azure_search_endpoint:
        raise AzureSearchConfigError("AZURE_SEARCH_ENDPOINT is not set")
    client_options = {}
    if settings.scaling_enabled:
        # Scale profile: Azure's default retry has no jitter and allows up to 10 retries.
        from app.scaling.retries import search_retry_policy

        client_options["retry_policy"] = search_retry_policy(settings)
    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index_name,
        credential=DefaultAzureCredential(),
        **client_options,
    )

def odata_string(value) -> str:
    """OData string literal: single-quoted, embedded quotes doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def build_metadata_filter(filters) -> str | None:
    """
    {'company_name': 'amazon', 'fiscal_year': 2023}
      -> "company_name eq 'amazon' and fiscal_year eq 2023"
    Only the known filter fields are accepted.
    """
    clauses = []
    for key, value in (filters or {}).items():
        if key in _STRING_FILTERS:
            clauses.append(f"{key} eq {odata_string(value)}")
        elif key in _INT_FILTERS:
            clauses.append(f"{key} eq {int(value)}")
        else:
            raise ValueError(f"Unsupported filter field: {key}")
    return " and ".join(clauses) or None


def build_keyword_query(ranking_keywords) -> str | None:
    """
    Keywords -> simple-syntax query: each keyword a quoted phrase, OR between them.
    ['net sales', 'operating income'] -> '"net sales" | "operating income"'
    Quotes and backslashes are removed, duplicates and keywords without any
    letter or digit are dropped.
    """
    phrases, seen = [], set()
    for keyword in ranking_keywords or []:
        cleaned = " ".join(str(keyword).replace('"', " ").replace("\\", " ").split())
        if not any(ch.isalnum() for ch in cleaned) or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        phrases.append(f'"{cleaned}"')
    return " | ".join(phrases) or None


def build_filter(filters, ranking_keywords, keyword_mode: str) -> str | None:
    """The full $filter: metadata equality, plus (hard mode) 'contains a keyword'."""
    clauses = []
    metadata_filter = build_metadata_filter(filters)
    if metadata_filter:
        clauses.append(metadata_filter)
    if keyword_mode == "hard":
        keyword_query = build_keyword_query(ranking_keywords)
        if keyword_query:
            clauses.append(
                f"search.ismatch({odata_string(keyword_query)}, '{TEXT_FIELD}', 'simple', 'any')"
            )
    return " and ".join(clauses) or None


def _to_document(hit) -> Document:
    metadata = {key: hit[key] for key in METADATA_FIELDS if hit.get(key) is not None}
    return Document(page_content=hit.get(TEXT_FIELD) or "", metadata=metadata)


def search_docs_azure(query, filters=None, ranking_keywords=None, k=3):
    """
    Same contract as vectorstore.search_docs(): returns up to k matching pages as
    Documents. The query is embedded with the same Gemini model as the Chroma path.

    Example:
        docs = search_docs_azure(
            query="Analyze cash flow",
            filters={"company_name": "amazon", "doc_type": "10-k"},
            ranking_keywords=["cash flow", "liquidity"],
            k=50,
        )
    """
    settings = get_settings()
    odata_filter = build_filter(filters, ranking_keywords, settings.azure_search_keyword_mode)
    keyword_query = build_keyword_query(ranking_keywords)

    vector_args = {
        "vector": get_embeddings().embed_query(query),
        "k_nearest_neighbors": k,
        "fields": VECTOR_FIELD,
    }
    if settings.azure_search_exhaustive:
        vector_args["exhaustive"] = True

    client = get_search_client()
    try:
        results = client.search(
            search_text=keyword_query,
            filter=odata_filter,
            top=k,
            select=[TEXT_FIELD, *METADATA_FIELDS],
            vector_queries=[VectorizedQuery(**vector_args)],
            vector_filter_mode="preFilter",
            query_type="simple",
            search_mode="any",
        )
        return [_to_document(hit) for hit in results]
    except AzureError as exc:
        logger.error("Azure AI Search request failed: %s", exc)
        raise AzureSearchError("Azure AI Search request failed") from exc
