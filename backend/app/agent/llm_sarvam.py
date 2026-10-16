"""Sarvam adapter (OpenAI-compatible chat completions over plain HTTPS, via httpx).

No provider SDK: Sarvam's chat API follows the OpenAI request/response shape, so tools and tool
calls need no translation. Failure mapping is what matters for the model chain:

- 429 with a retry delay          -> RateLimited (the last chain entry waits, others fail over)
- credits/quota exhausted         -> LLMUnavailable with a long retry_after, so the chain skips
                                     Sarvam for an hour instead of failing every call
- 401/403 (bad or revoked key)    -> the same long skip, reason "auth_failed"
- 5xx / network / timeout         -> LLMUnavailable (retried only when this is the last entry)

Credits are one-time, so exhaustion is an expected state, not an incident.

Models (checked against the live API, 2026-10-17): `sarvam-30b` is deprecated (HTTP 400).
`sarvam-105b-conversations` answers directly in ~0.5 s and is the default; `sarvam-105b` is a
reasoning model (~3 s, 400-600 hidden reasoning tokens per reply), too slow for live voice.
"""

import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from app.agent.llm_client import (
    LLMResponse,
    LLMUnavailable,
    RateLimited,
    ToolCall,
    call_with_rate_limit_budget,
    strip_private_keys,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.sarvam.ai/v1"
# How long the chain skips Sarvam after exhausted credits or a rejected key (> the chain's 60 s
# cooldown threshold, so FallbackLLMClient stops calling it until then).
SKIP_SECONDS = 3600.0
MAX_TOKENS = 2048
_EXHAUSTED = re.compile(r"credit|quota|insufficient|balance|payment|billing", re.I)
_THINKING = re.compile(r"<think>.*?</think>", re.S)


class SarvamLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        temperature: float = 0.2,
        rate_limit_max_wait_seconds: float = 0.0,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.name = f"sarvam:{model}"
        self._temperature = temperature
        self._max_retries = max_retries
        self._rate_limit_budget = rate_limit_max_wait_seconds
        self._sleep = sleep
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {
            # Sarvam's docs show both styles; sending both is harmless and survives either.
            "Authorization": f"Bearer {api_key}",
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [strip_private_keys(m) for m in messages],
            "temperature": self._temperature,
            # Explicit: sarvam-105b is a reasoning model that spends hundreds of hidden tokens
            # thinking, and with the server default limit it returned an empty reply (measured).
            "max_tokens": MAX_TOKENS,
        }
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        data = call_with_rate_limit_budget(
            lambda: self._post(body), self._rate_limit_budget, self._sleep
        )
        return parse_response(data, self.name)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(self._url, headers=self._headers, json=body)
            except httpx.TimeoutException as exc:
                raise LLMUnavailable("timeout") from exc
            except httpx.TransportError as exc:
                if attempt < self._max_retries:
                    self._sleep(0.5 * (attempt + 1))
                    continue
                raise LLMUnavailable("connection_error") from exc
            if response.status_code >= 500 and attempt < self._max_retries:
                self._sleep(0.5 * (attempt + 1))
                continue
            return self._checked(response)
        raise AssertionError("unreachable")

    def _checked(self, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status < 400:
            data = response.json()
            if not isinstance(data, dict):
                raise LLMUnavailable("bad_response")
            return data
        text = response.text[:500]
        logger.warning("sarvam_error", extra={"status": status})  # body may echo input: not logged
        if status in (401, 403):
            raise LLMUnavailable("auth_failed", retry_after=SKIP_SECONDS)
        if status == 402 or (status in (400, 429) and _EXHAUSTED.search(text)):
            raise LLMUnavailable("credits_exhausted", retry_after=SKIP_SECONDS)
        if status == 429:
            raise RateLimited(_retry_after(response))
        raise LLMUnavailable(f"http_{status}")


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None


def parse_response(data: Mapping[str, Any], provider: str) -> LLMResponse:
    choices = data.get("choices") or []
    if not choices:
        return LLMResponse(content=None, provider=provider, usage=_usage(data))
    message = choices[0].get("message") or {}
    calls = []
    for i, raw in enumerate(message.get("tool_calls") or []):
        function = raw.get("function") or {}
        arguments = function.get("arguments")
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{i}"),
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
            )
        )
    content = message.get("content")
    if isinstance(content, str):
        # Reasoning models may inline their thinking; replies are read aloud, so drop it.
        content = _THINKING.sub("", content).strip() or None
    return LLMResponse(
        content=content, tool_calls=tuple(calls), provider=provider, usage=_usage(data)
    )


def _usage(data: Mapping[str, Any]) -> dict[str, int] | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    counts = {"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}
    return {k: int(v) for k, v in counts.items() if isinstance(v, int)} or None
