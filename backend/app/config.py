from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


# All settings in one place, read from .env (Gemini key, models, row limit, timeout, max repairs).
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_provider: str = "gemini"  # gemini | openai
    gemini_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = "gemini-3.5-flash-lite"
    # Tried in order when the main model is overloaded, rate limited or times out
    llm_fallback_models: str = "gemini-flash-lite-latest,gemini-3.8-flash,gemini-3.5-flash"
    llm_timeout_seconds: float = 30

    # The API key for whichever LLM provider is selected.
    @property
    def llm_key(self) -> str:
        return self.gemini_api_key if self.llm_provider == "gemini" else self.openai_api_key

    # Local speech
    whisper_model: str = "base.en"
    piper_voice: str = "en_US-lessac-medium"

    # Demo databases (read-only users), filled in by docker compose
    demo_postgres_url: str = ""
    demo_mysql_url: str = ""
    demo_sqlite_url: str = ""

    # Safety
    query_row_limit: int = 1000
    query_timeout_seconds: int = 15
    max_repair_attempts: int = 3

    data_dir: str = "/data"


# Gives every file the same Settings object (created once).
@lru_cache
def get_settings() -> Settings:
    return Settings()
