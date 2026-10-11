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

    # Model chain in priority order, "provider[:model]" entries; each is tried until one answers.
    # Replaces the blueprint's Anthropic API. Free-tier quotas are per model, so several models per
    # provider add headroom. Entries whose provider has no key are skipped (see CHANGE_LOG.md).
    llm_providers: str = (
        "gemini:gemini-3.8-flash,gemini:gemini-3.5-flash-lite,"
        "groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b"
    )
    gemini_api_key: SecretStr | None = None
    # Stable Gemini Flash model recommended for new projects (checked 2026-10-11).
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key: SecretStr | None = None
    # Production Groq model with tool calling (alternative available to this key: gpt-oss-20b).
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2
    # Total time one model call may spend waiting out provider rate limits (429 + retry-after).
    llm_rate_limit_max_wait_seconds: float = 30.0

    # Voice (Phase 4). LiveKit dev-mode credentials: local development only.
    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: SecretStr = SecretStr("secret")
    # faster-whisper: base.en on CPU int8 measured ~0.13x real time on the dev laptop (2026-10-11).
    stt_model: str = "base.en"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str = "en"
    # Piper voice trained on LJSpeech (public-domain dataset), 22.05 kHz.
    tts_voice_path: Path = REPO_ROOT / "models" / "piper" / "en_US-ljspeech-medium.onnx"
    models_dir: Path = REPO_ROOT / "models"
    voice_web_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment / .env
