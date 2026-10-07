from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.config import get_settings

LLM_MODEL = "gemini-3-flash-preview"
EMBEDDING_MODEL = "gemini-embedding-001"


@lru_cache
def get_llm() -> ChatGoogleGenerativeAI:
    """Plain LLM — router, sql agent, web-search agent, code-execution agent."""
    settings = get_settings()
    return ChatGoogleGenerativeAI(model=LLM_MODEL, google_api_key=settings.google_api_key)


@lru_cache
def get_reasoning_llm() -> ChatGoogleGenerativeAI:
    """Reasoning-enabled LLM — self-RAG nodes (grade / generate / transform / hallucination checks)."""
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=settings.google_api_key,
        thinking_level="medium",
        include_thoughts=True,
    )


@lru_cache
def get_sql_llm() -> ChatGoogleGenerativeAI:
    """LLM used by the SQL tools to generate / fix SQL queries."""
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL,
        google_api_key=settings.google_api_key,
        thinking_level="medium",
    )


@lru_cache
def get_embeddings() -> GoogleGenerativeAIEmbeddings:
    settings = get_settings()
    return GoogleGenerativeAIEmbeddings(model=EMBEDDING_MODEL, google_api_key=settings.google_api_key)