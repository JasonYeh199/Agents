from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Keep a single repository-level .env usable regardless of whether uvicorn is
# launched from the repository root or from apps/api (the documented workflow).
WORKING_DIRECTORY_ENV = Path.cwd() / ".env"
REPOSITORY_ENV = next(
    (parent / ".env" for parent in Path(__file__).resolve().parents if (parent / ".env").is_file()),
    WORKING_DIRECTORY_ENV,
)


class Settings(BaseSettings):
    app_name: str = "SignalForge Research API"
    database_url: str = "sqlite+aiosqlite:///./research.db"
    object_store_path: Path = Path(".data/objects")
    object_store_backend: str = "filesystem"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "research-documents"
    s3_access_key: str = "research"
    s3_secret_key: str = "research-secret"
    model_provider: str = "deterministic"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"
    reasoning_effort: str = "medium"
    max_tool_calls: int = 20
    max_output_tokens: int = 6000
    web_origin: str = "http://localhost:3000"
    fetch_timeout_seconds: float = 20
    fetch_max_bytes: int = 15_000_000
    step_timeout_seconds: float = 45
    max_step_retries: int = 2
    admin_token: str = ""
    admin_session_secret: str = ""
    admin_cookie_secure: bool = True
    admin_session_hours: int = 8
    sec_user_agent: str = ""
    universe_auto_sync: bool = True
    universe_sync_hour_utc: int = 18
    fixture_mode: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        """Make Render's standard Postgres URL usable by SQLAlchemy asyncio."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    model_config = SettingsConfigDict(
        env_file=(REPOSITORY_ENV, WORKING_DIRECTORY_ENV),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
