from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
