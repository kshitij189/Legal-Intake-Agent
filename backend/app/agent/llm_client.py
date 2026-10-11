"""Provider-neutral LLM interface, fallback chain and factory.

The conversation keeps one history in OpenAI-style messages. Each adapter (`llm_gemini`,
`llm_groq`) converts that history to its own format. Opaque per-model state (e.g. Gemini thought
signatures) travels on assistant messages under `_provider_state`, keyed by the producing
model's chain name ("gemini:<model>"), and is ignored by every other model. Only the adapter
modules import provider SDKs.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import Settings

logger = logging.getLogger(__name__)

PROVIDER_STATE_KEY = "_provider_state"
DEFAULT_RATE_LIMIT_DELAY = 5.0
# A 429 asking us to wait longer than this is a quota exhaustion (e.g. a daily limit): the model
# is skipped until the quota resets instead of being retried on every call.
COOLDOWN_THRESHOLD_SECONDS = 60.0
PROVIDERS = ("gemini", "groq")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text; validated by the tool executor, never trusted


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    provider: str = ""  # chain name of the model that answered, e.g. "gemini:gemini-3.8-flash"
    # Opaque data the same model needs back on the next request (never sent to others).
    provider_state: Any = None


class LLMUnavailable(Exception):
    """The model could not produce a response (timeout, rate limit, outage, bad credentials)."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class LLMClient(Protocol):
    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse: ...


class RateLimited(Exception):
    """Raised inside an adapter call; `delay` is the provider's suggested wait in seconds."""

    def __init__(self, delay: float | None) -> None:
        super().__init__(delay)
        self.delay = delay


def call_with_rate_limit_budget[T](
    call: Callable[[], T], budget_seconds: float, sleep: Callable[[float], None]
) -> T:
    """Retry `call` after RateLimited, waiting as suggested, within a total time budget."""
    waited = 0.0
    while True:
        try:
            return call()
        except RateLimited as exc:
            delay = max(1.0, exc.delay or DEFAULT_RATE_LIMIT_DELAY)
            if waited + delay > budget_seconds:
                raise LLMUnavailable("rate_limited", retry_after=exc.delay) from exc
            logger.warning("llm_rate_limited_waiting", extra={"delay_s": delay})
            sleep(delay)
            waited += delay


def strip_private_keys(message: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in message.items() if not k.startswith("_")}


class FallbackLLMClient:
    """Tries models in order; the first successful response wins. A model whose quota is
    exhausted (429 with a long retry delay) is skipped until the provider says it resets."""

    def __init__(
        self,
        entries: Sequence[tuple[str, LLMClient]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not entries:
            raise ValueError("at least one model is required")
        self._entries = list(entries)
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}
        self.last_provider: str | None = None

    @property
    def provider_names(self) -> list[str]:
        return [name for name, _ in self._entries]

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        failures = []
        for name, client in self._entries:
            now = self._clock()
            if self._cooldown_until.get(name, 0.0) > now:
                failures.append(f"{name}:quota_cooldown")
                continue
            try:
                response = client.complete(messages, tools)
            except LLMUnavailable as exc:
                logger.warning("llm_model_failed", extra={"model": name, "reason": exc.reason})
                failures.append(f"{name}:{exc.reason}")
                if exc.retry_after is not None and exc.retry_after > COOLDOWN_THRESHOLD_SECONDS:
                    self._cooldown_until[name] = now + exc.retry_after
                continue
            self.last_provider = name
            return response
        raise LLMUnavailable("; ".join(failures))


def _key(secret: Any) -> str | None:
    value: str = secret.get_secret_value() if secret is not None else ""
    return value if value and value != "replace-me" else None


def parse_chain(spec: str, settings: Settings) -> list[tuple[str, str]]:
    """'gemini:gemini-3.8-flash,groq' -> [('gemini', 'gemini-3.8-flash'), ('groq', <GROQ_MODEL>)].
    Model IDs may contain '/' (e.g. openai/gpt-oss-120b); only the first ':' separates."""
    chain = []
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        provider, _, model = entry.partition(":")
        provider = provider.strip().lower()
        if provider not in PROVIDERS:
            raise ValueError(f"unknown LLM provider {provider!r} in LLM_PROVIDERS")
        default = settings.gemini_model if provider == "gemini" else settings.groq_model
        chain.append((provider, model.strip() or default))
    return chain


def build_llm_client(settings: Settings, sleep: Callable[[float], None] = time.sleep) -> LLMClient:
    """Model chain from LLM_PROVIDERS. Entries whose provider has no API key are skipped. Only the
    last entry waits out short rate limits; earlier ones fail over at once."""
    from app.agent.llm_gemini import GeminiLLMClient  # adapters import provider SDKs
    from app.agent.llm_groq import GroqLLMClient

    keys = {"gemini": _key(settings.gemini_api_key), "groq": _key(settings.groq_api_key)}
    requested = parse_chain(settings.llm_providers, settings)
    usable = [(p, m) for p, m in requested if keys[p]]
    for provider in {p for p, _ in requested} - {p for p, _ in usable}:
        logger.warning("llm_provider_skipped_no_key", extra={"provider": provider})
    if not usable:
        names = ", ".join(dict.fromkeys(p for p, _ in requested))
        raise LLMUnavailable(f"no API key configured for any of: {names}")

    entries: list[tuple[str, LLMClient]] = []
    for i, (provider, model) in enumerate(usable):
        adapter = GeminiLLMClient if provider == "gemini" else GroqLLMClient
        client = adapter(
            api_key=keys[provider] or "",
            model=model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            rate_limit_max_wait_seconds=(
                settings.llm_rate_limit_max_wait_seconds if i == len(usable) - 1 else 0.0
            ),
            sleep=sleep,
        )
        entries.append((client.name, client))
    return FallbackLLMClient(entries)
