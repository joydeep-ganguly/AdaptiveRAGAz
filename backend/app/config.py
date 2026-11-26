# backend/app/config.py

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at backend/app/config.py, so two parents up is backend/ —
# the root every relative storage path in .env is resolved against.
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """
    Centralised application configuration.

    Values are read once from environment variables / the .env file.
    Every other module should import `get_settings()` from here instead of
    calling `os.getenv(...)` or `load_dotenv()` itself.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # LLM / embeddings / tools — no defaults on purpose: startup should
    # fail immediately if these are missing, not fail on the first request.
    # ------------------------------------------------------------------ #
    google_api_key: str
    e2b_api_key: str

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # ------------------------------------------------------------------ #
    # Storage paths — kept as the raw relative strings from .env here;
    # use the resolved_* properties below everywhere else in the codebase.
    # ------------------------------------------------------------------ #
    app_db_path: str = "storage/sqlite/app.db"
    checkpoint_db_path: str = "storage/sqlite/checkpoints.db"
    employees_db_path: str = "storage/sqlite/employees_db-full-1.0.6.db"
    chroma_dir: str = "storage/chroma_financial_db"
    rag_data_dir: str = "data/rag_data"
    debug_logs_dir: str = "storage/debug_logs"
    media_dir: str = "storage/media"

    # ------------------------------------------------------------------ #
    # Azure Blob Storage — code-execution datasets.
    # All optional at startup so tests/imports never need Azure; the account
    # name is validated when the blob client is first built.
    # Auth is DefaultAzureCredential (az login locally, managed identity in Azure).
    # ------------------------------------------------------------------ #
    azure_storage_account_name: str | None = None
    azure_storage_container: str = "code-execution"
    azure_storage_code_data_prefix: str = "code_data/"
    code_data_max_upload_mb: int = 50

    # ------------------------------------------------------------------ #
    # Azure Blob Storage — RAG filing PDFs. Same account and auth as the
    # code-data settings above, separate container. Blob names are
    # {prefix}{company}/{company} {doc_type} [{quarter}] {year}.pdf
    # ------------------------------------------------------------------ #
    azure_storage_rag_container: str = "rag"
    azure_storage_rag_data_prefix: str = "rag_data/"
    rag_data_max_upload_mb: int = 20
    
    # ------------------------------------------------------------------ #
    # Azure AI Search — RAG retrieval (Sub-Plan 4). retrieval_backend picks
    # where retriever_docs reads from: "chroma" (local, default) or "azure".
    # All optional at startup, so tests and local runs never need Azure.
    # Auth is DefaultAzureCredential (az login locally, managed identity in
    # Azure); the identity needs "Search Index Data Reader" on the service.
    # Keyword mode: "hard" = a page must contain at least one ranking keyword
    # (like Chroma's $contains); "hybrid" = keywords only add to the score.
    # ------------------------------------------------------------------ #
    retrieval_backend: Literal["chroma", "azure"] = "chroma"
    azure_search_endpoint: str | None = None
    azure_search_index_name: str = "idx-adaptiverag-trial"
    azure_search_keyword_mode: Literal["hard", "hybrid"] = "hard"
    azure_search_exhaustive: bool = False

    # ------------------------------------------------------------------ #
    # Foundry web search agent (Azure AI Foundry project).
    # Optional at startup: a blank endpoint means "use DuckDuckGo only", so
    # tests and local runs never need Azure. Auth is DefaultAzureCredential
    # (az login locally, managed identity in Azure).
    # ------------------------------------------------------------------ #
    foundry_project_endpoint: str | None = None
    foundry_web_search_agent_name: str = "adaptiverag-websearch"
    foundry_request_timeout_seconds: int = 45
    web_search_fallback_enabled: bool = True

    # ------------------------------------------------------------------ #
    # Scaling layer (app/scaling/). OFF by default: with scaling_enabled=false
    # the app behaves exactly as before — synchronous /chat/query, no Redis,
    # no worker. Turn it on only for the queue-based deployment profile.
    # ------------------------------------------------------------------ #
    scaling_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"

    # Queue: jobs waiting beyond this depth are rejected (load shedding)
    scaling_queue_max_depth: int = 200
    scaling_job_result_ttl_seconds: int = 3600

    # Per-user token bucket: burst size, and how many requests are refilled per second
    scaling_rate_limit_capacity: int = 10
    scaling_rate_limit_refill_per_second: float = 0.5

    # Idempotency keys and answer cache
    scaling_idempotency_ttl_seconds: int = 86400
    scaling_cache_ttl_seconds: int = 3600

    # LLM token budgets: per request, per user per day, and the cap on the
    # grade -> rewrite -> retrieve loop inside one request
    scaling_max_tokens_per_request: int = 60000
    scaling_max_tokens_per_user_per_day: int = 500000
    scaling_max_rewrites_per_request: int = 2
    scaling_max_llm_calls_per_request: int = 20  # a normal RAG question uses about 7

    # Retries on model and search calls: exponential backoff with jitter
    scaling_retry_max_attempts: int = 4
    scaling_retry_base_delay_seconds: float = 1.0
    scaling_retry_max_delay_seconds: float = 20.0

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #
    debug_mode: bool = False

    # ------------------------------------------------------------------ #
    # Resolved absolute paths
    # ------------------------------------------------------------------ #
    @property
    def resolved_app_db_path(self) -> Path:
        return (BASE_DIR / self.app_db_path).resolve()

    @property
    def resolved_checkpoint_db_path(self) -> Path:
        return (BASE_DIR / self.checkpoint_db_path).resolve()

    @property
    def resolved_employees_db_path(self) -> Path:
        return (BASE_DIR / self.employees_db_path).resolve()

    @property
    def resolved_chroma_dir(self) -> Path:
        return (BASE_DIR / self.chroma_dir).resolve()

    @property
    def resolved_rag_data_dir(self) -> Path:
        return (BASE_DIR / self.rag_data_dir).resolve()

    @property
    def resolved_debug_logs_dir(self) -> Path:
        return (BASE_DIR / self.debug_logs_dir).resolve()

    @property
    def resolved_media_dir(self) -> Path:
        return (BASE_DIR / self.media_dir).resolve()

    @property
    def sqlalchemy_database_url(self) -> str:
        """Used by backend/app/db/session.py to open the users/conversations DB."""
        return f"sqlite:///{self.resolved_app_db_path}"

    def ensure_storage_dirs(self) -> None:
        """Create any storage directories that don't exist yet. Call once at app startup."""
        for path in (
            self.resolved_app_db_path.parent,
            self.resolved_checkpoint_db_path.parent,
            self.resolved_chroma_dir,
            self.resolved_rag_data_dir,
            self.resolved_debug_logs_dir,
            self.resolved_media_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached Settings instance so the .env file is parsed only once
    per process. Import and call this everywhere a config value is needed:

        from app.config import get_settings
        settings = get_settings()
    """
    return Settings()