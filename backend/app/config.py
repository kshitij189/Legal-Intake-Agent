from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str
    test_database_url: str | None = None
    demo_firm_slug: str = "northstar-legal"

    # LLM provider: Groq (replaces the blueprint's Anthropic API - see CHANGE_LOG.md).
    groq_api_key: SecretStr | None = None
    # Production Groq model with tool calling; any tool-capable Groq model ID can be used.
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment / .env
