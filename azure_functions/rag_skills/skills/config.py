"""Settings read from the Function app's application settings (environment)."""

import os

from skills.errors import SkillError


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SkillError(f"The application setting {name} is not set")
    return value


def document_intelligence_endpoint() -> str:
    return _required("DOCUMENT_INTELLIGENCE_ENDPOINT").rstrip("/")


def gemini_api_key() -> str:
    return _required("GEMINI_API_KEY")


def allowed_blob_host() -> str:
    return _required("ALLOWED_BLOB_HOST").lower()


def embedding_model() -> str:
    return (os.environ.get("EMBEDDING_MODEL") or "gemini-embedding-001").strip()


def di_chunk_pages() -> int:
    return max(1, int(os.environ.get("DI_CHUNK_PAGES") or 20))


def di_parallelism() -> int:
    return max(1, int(os.environ.get("DI_PARALLELISM") or 6))
