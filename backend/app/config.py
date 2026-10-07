# backend/app/config.py

from functools import lru_cache
from pathlib import Path

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