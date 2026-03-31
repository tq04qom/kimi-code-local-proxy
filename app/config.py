from functools import lru_cache

from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    local_api_key: str = Field(..., alias="LOCAL_API_KEY")
    upstream_provider: str = Field("kimi_cli", alias="UPSTREAM_PROVIDER")
    upstream_base_url: str = Field("", alias="UPSTREAM_BASE_URL")
    upstream_api_key: str = Field("", alias="UPSTREAM_API_KEY")
    upstream_model: str = Field("kimi-auto", alias="UPSTREAM_MODEL")
    kimi_cli_path: str = Field("", alias="KIMI_CLI_PATH")
    kimi_cli_work_dir: str = Field("", alias="KIMI_CLI_WORK_DIR")
    kimi_cli_passthrough_model: bool = Field(
        False,
        validation_alias=AliasChoices("KIMI_CLI_PASSTHROUGH_MODEL"),
    )
    max_concurrent_requests: int = Field(2, alias="MAX_CONCURRENT_REQUESTS")
    max_queue_wait_seconds: float = Field(30.0, alias="MAX_QUEUE_WAIT_SECONDS")
    rate_limit_max_requests: int = Field(60, alias="RATE_LIMIT_MAX_REQUESTS")
    rate_limit_window_seconds: int = Field(60, alias="RATE_LIMIT_WINDOW_SECONDS")
    log_dir: str = Field("logs", alias="LOG_DIR")
    session_store_path: str = Field("", alias="SESSION_STORE_PATH")
    request_timeout_seconds: float = Field(120.0, alias="REQUEST_TIMEOUT_SECONDS")
    host: str = Field("127.0.0.1", alias="HOST")
    port: int = Field(8000, alias="PORT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
