"""Groq adapter (OpenAI-compatible chat completions). The only module importing the Groq SDK."""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

import groq

from app.agent.llm_client import (
    LLMResponse,
    LLMUnavailable,
    RateLimited,
    ToolCall,
    call_with_rate_limit_budget,
    strip_private_keys,
)

logger = logging.getLogger(__name__)


class GroqLLMClient:
    # Groq answers 400 "tool_use_failed" when the model emits a malformed tool call; one fresh
    # attempt usually succeeds. Network/5xx retries are handled by the SDK (`max_retries`).
    TOOL_USE_FAILED_ATTEMPTS = 2

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        temperature: float = 0.2,
        rate_limit_max_wait_seconds: float = 0.0,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.name = f"groq:{model}"
        self._temperature = temperature
        self._rate_limit_budget = rate_limit_max_wait_seconds
        self._sleep = sleep
        self._client = client or groq.Groq(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        payload = [strip_private_keys(m) for m in messages]
        response = call_with_rate_limit_budget(
            lambda: self._create(payload, tools), self._rate_limit_budget, self._sleep
        )
        message = response.choices[0].message
        calls = tuple(
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (message.tool_calls or [])
        )
        return LLMResponse(content=message.content, tool_calls=calls, provider=self.name)

    def _create(self, messages: list[dict[str, Any]], tools: Sequence[Mapping[str, Any]]) -> Any:
        for attempt in range(1, self.TOOL_USE_FAILED_ATTEMPTS + 1):
            try:
                return self._client.chat.completions.create(
                    model=self.model,
                    messages=cast(Any, messages),
                    tools=cast(Any, list(tools)),
                    tool_choice="auto",
                    temperature=self._temperature,
                )
            except groq.BadRequestError as exc:
                if "tool_use_failed" in str(exc) and attempt < self.TOOL_USE_FAILED_ATTEMPTS:
                    logger.warning("llm_tool_use_failed_retrying")
                    continue
                raise LLMUnavailable(f"bad request: {_error_code(exc)}") from exc
            except groq.APITimeoutError as exc:
                raise LLMUnavailable("timeout") from exc
            except groq.RateLimitError as exc:
                raise RateLimited(_retry_after_seconds(exc)) from exc
            except groq.APIConnectionError as exc:
                raise LLMUnavailable("connection_error") from exc
            except groq.APIStatusError as exc:
                raise LLMUnavailable(f"http_{exc.status_code}") from exc
        raise AssertionError("unreachable")


def _retry_after_seconds(exc: groq.APIStatusError) -> float | None:
    try:
        return float(exc.response.headers.get("retry-after", ""))
    except ValueError:
        return None


def _error_code(exc: groq.APIStatusError) -> str:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    return str(error.get("code") or exc.status_code) if isinstance(error, dict) else "unknown"
