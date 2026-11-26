from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.embeddings import Embeddings

from app.config import get_settings

LLM_MODEL = "gemini-3-flash-preview"
EMBEDDING_MODEL = "gemini-embedding-001"


def _retry_options() -> dict:
    """Scale profile only: set how many attempts the Gemini client makes (it already
    backs off with jitter). The plain profile keeps the library default."""
    settings = get_settings()
    if settings.scaling_enabled:
        return {"max_retries": max(1, settings.scaling_retry_max_attempts)}
    return {}


@lru_cache
def get_llm() -> ChatGoogleGenerativeAI:
    """Plain LLM — router, sql agent, web-search agent, code-execution agent."""
    settings = get_settings()
    return ChatGoogleGenerativeAI(model=LLM_MODEL, google_api_key=settings.google_api_key, **_retry_options())


@lru_cache
def get_reasoning_llm() -> ChatGoogleGenerativeAI:
    """Reasoning-enabled LLM — self-RAG nodes (grade / generate / transform / hallucination checks)."""
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=settings.google_api_key,
        thinking_level="medium",
        include_thoughts=True,
        **_retry_options(),
    )


@lru_cache
def get_sql_llm() -> ChatGoogleGenerativeAI:
    """LLM used by the SQL tools to generate / fix SQL queries."""
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=settings.google_api_key,
        thinking_level="medium",
        **_retry_options(),
    )


@lru_cache
def get_embeddings() -> Embeddings:
    settings = get_settings()
    embeddings = GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL, google_api_key=settings.google_api_key)
    if settings.scaling_enabled:
        # Scale profile only: remember embeddings in Redis, and retry transient failures
        # (the Gemini embeddings client does not). A cache hit never reaches the retry.
        # Imported here so the small setup never loads the scaling package.
        from app.scaling.cache import cached_embeddings
        from app.scaling.retries import retrying_embeddings

        return retrying_embeddings(cached_embeddings(embeddings), settings)
    return embeddings