from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    # Sarvam (optional; OpenAI-compatible chat API, one-time credits). Add "sarvam" or
    # "sarvam:<model>" to LLM_PROVIDERS to use it; with no key the entry is skipped.
    sarvam_api_key: SecretStr | None = None
    sarvam_model: str = "sarvam-30b"
    sarvam_base_url: str = "https://api.sarvam.ai/v1"
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2
    # Total time one model call may spend waiting out provider rate limits (429 + retry-after).
    llm_rate_limit_max_wait_seconds: float = 30.0

    # Voice (Phase 4). LiveKit dev-mode credentials: local development only.
    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: SecretStr = SecretStr("secret")
    # faster-whisper base.en: CPU int8 ~0.9 s, CUDA float16 ~0.25 s per 11 s utterance
    # (measured 2026-10-12). "auto" = CUDA when available, else CPU.
    stt_model: str = "base.en"
    stt_device: str = "auto"
    stt_compute_type: str = "auto"
    stt_language: str = "en"
    # "auto" = Kokoro when its GPU path works, else Piper; or force "piper" / "kokoro".
    tts_engine: Literal["auto", "piper", "kokoro"] = "auto"
    # Piper voice trained on LJSpeech (public-domain dataset), 22.05 kHz.
    tts_voice_path: Path = REPO_ROOT / "models" / "piper" / "en_US-ljspeech-medium.onnx"
    # Kokoro-82M v1.0 (Apache-2.0 weights), 24 kHz; af_heart is the highest-graded voice.
    kokoro_model_path: Path = REPO_ROOT / "models" / "kokoro" / "kokoro-v1.0.onnx"
    kokoro_voices_path: Path = REPO_ROOT / "models" / "kokoro" / "voices-v1.0.bin"
    kokoro_voice: str = "af_heart"
    # `auto` uses Kokoro only on GPUs with at least this much memory. Measured 2026-10-12: ~2 GB
    # per voice process, active + one spare; a 4 GB card saturated and calls degraded.
    kokoro_min_gpu_mib: int = 6144
    models_dir: Path = REPO_ROOT / "models"
    voice_web_port: int = 8000
    # Pre-spawned job processes; each loads its own STT/TTS models. One keeps a 4 GB GPU from
    # being oversubscribed (several idle processes filled 3.9 of 4 GB, measured 2026-10-12).
    voice_idle_processes: int = 1

    # Durable workflows (Phase 5). Local dev server from docker compose.
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "legal-intake"
    # In-call bookings run as a Temporal workflow (timeout -> state check -> bounded retry).
    booking_via_workflow: bool = True
    booking_wait_seconds: float = 12.0
    # Fault injection for tests and the failure demo only - see app/workflows/faults.py.
    calendar_fault_injection: str | None = None
    notifier_fault_injection: str | None = None

    # Review dashboard (Phase 7). Local demo: HTTP on 127.0.0.1, so the cookie is not `Secure`.
    dashboard_cookie_secure: bool = False
    # Nudge the running handoff workflow after accept/resolve (best effort; DB is authoritative).
    handoff_signal_enabled: bool = True
    # Password given to the fictional staff by scripts/seed_demo_data.py (unset = no logins).
    demo_staff_password: SecretStr | None = None

    # Langfuse tracing (optional; self-hosted stack in infra/langfuse). Off unless enabled and keys
    # are set. Without content capture, traces hold structure, timings, tool names, statuses and
    # token counts, but no caller text, model messages or tool arguments.
    langfuse_enabled: bool = False
    # 127.0.0.1, not localhost: on Windows localhost tries IPv6 first (~2 s per connection).
    langfuse_base_url: str = "http://127.0.0.1:3100"
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_capture_content: bool = False
    langfuse_environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment / .env
