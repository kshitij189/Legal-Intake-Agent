import pytest

from app.config import Settings


def test_groq_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/x")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_secret_value")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.groq_model == "llama-3.3-70b-versatile"
    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == "gsk_test_secret_value"
    # The key must never leak through logging or reprs.
    assert "gsk_test_secret_value" not in repr(settings)
    assert "gsk_test_secret_value" not in str(settings.model_dump())


def test_groq_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/x")
    for name in ("GROQ_API_KEY", "GROQ_MODEL", "LLM_TIMEOUT_SECONDS", "LLM_MAX_RETRIES"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.groq_api_key is None
    assert settings.groq_model == "openai/gpt-oss-120b"
    assert (settings.llm_timeout_seconds, settings.llm_max_retries) == (20.0, 2)
