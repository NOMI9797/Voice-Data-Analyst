from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"

    # Local speech
    whisper_model: str = "base.en"
    piper_voice: str = "en_US-lessac-medium"

    # Demo databases (read-only users), filled in by docker compose
    demo_postgres_url: str = ""
    demo_mysql_url: str = ""

    # Safety
    query_row_limit: int = 1000
    query_timeout_seconds: int = 15
    max_repair_attempts: int = 3

    data_dir: str = "/data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
