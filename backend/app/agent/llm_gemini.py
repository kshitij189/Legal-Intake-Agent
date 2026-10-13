"""Gemini adapter using Google's official `google-genai` SDK (native function calling).

Gemini requires its thought signatures to be sent back exactly as received in multi-turn function
calling. The adapter therefore returns the model's `Content` as `provider_state`, and replays it
verbatim on the next request. Turns produced by another provider (after a fallback) have no
signature; they get Google's documented placeholder signature instead.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.agent.llm_client import (
    PROVIDER_STATE_KEY,
    LLMResponse,
    LLMUnavailable,
    RateLimited,
    ToolCall,
    call_with_rate_limit_budget,
)

logger = logging.getLogger(__name__)

PROVIDER = "gemini"
# Placeholder accepted by the Gemini API for function calls it did not generate itself.
FOREIGN_THOUGHT_SIGNATURE = b"skip_thought_signature_validator"
_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


class GeminiLLMClient:
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
        self.name = f"{PROVIDER}:{model}"
        self._temperature = temperature
        self._rate_limit_budget = rate_limit_max_wait_seconds
        self._sleep = sleep
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                # 429 is excluded on purpose: rate limits are handled by the budget logic below.
                retry_options=types.HttpRetryOptions(
                    attempts=max_retries + 1, http_status_codes=[500, 502, 503, 504]
                ),
            ),
        )

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        system, contents = to_gemini_contents(messages, own_key=self.name)
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._temperature,
            tools=[types.Tool(function_declarations=to_function_declarations(tools))]
            if tools
            else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        response = call_with_rate_limit_budget(
            lambda: self._generate(contents, config), self._rate_limit_budget, self._sleep
        )
        return from_gemini_response(response, provider=self.name)

    def _generate(
        self, contents: list[types.Content], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        try:
            return self._client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except errors.APIError as exc:
            if exc.code == 429:
                raise RateLimited(_retry_delay(exc)) from exc
            raise LLMUnavailable(f"http_{exc.code}") from exc
        except httpx.TimeoutException as exc:
            raise LLMUnavailable("timeout") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable("connection_error") from exc


def to_function_declarations(
    tools: Sequence[Mapping[str, Any]],
) -> list[types.FunctionDeclaration]:
    return [
        types.FunctionDeclaration(
            name=t["function"]["name"],
            description=t["function"].get("description"),
            parameters_json_schema=t["function"].get("parameters"),
        )
        for t in tools
    ]


def to_gemini_contents(
    messages: Sequence[Mapping[str, Any]], own_key: str
) -> tuple[str | None, list[types.Content]]:
    """Convert the neutral (OpenAI-style) history into Gemini contents. Only turns produced by
    this exact model (`own_key`) are replayed verbatim: signatures are model-specific."""
    system: str | None = None
    contents: list[types.Content] = []
    tool_names: dict[str, str] = {}

    for message in messages:
        role = message["role"]
        if role == "system":
            system = message["content"]
        elif role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=message["content"])]))
        elif role == "assistant":
            calls = message.get("tool_calls") or []
            for call in calls:
                tool_names[call["id"]] = call["function"]["name"]
            own = (message.get(PROVIDER_STATE_KEY) or {}).get(own_key)
            if own is not None:
                contents.append(own)  # verbatim, including thought signatures
                continue
            parts: list[types.Part] = []
            if message.get("content"):
                parts.append(types.Part(text=message["content"]))
            for i, call in enumerate(calls):
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=call["function"]["name"],
                            args=json.loads(call["function"]["arguments"] or "{}"),
                        ),
                        thought_signature=FOREIGN_THOUGHT_SIGNATURE if i == 0 else None,
                    )
                )
            if parts:
                contents.append(types.Content(role="model", parts=parts))
        elif role == "tool":
            part = types.Part(
                function_response=types.FunctionResponse(
                    name=tool_names.get(message["tool_call_id"], "unknown_tool"),
                    response={"result": json.loads(message["content"])},
                )
            )
            previous = contents[-1] if contents else None
            if (
                previous is not None
                and previous.role == "user"
                and previous.parts
                and all(p.function_response is not None for p in previous.parts)
            ):
                previous.parts.append(part)  # parallel results share one turn
            else:
                contents.append(types.Content(role="user", parts=[part]))
    return system, contents


def from_gemini_response(response: types.GenerateContentResponse, provider: str) -> LLMResponse:
    candidate = response.candidates[0] if response.candidates else None
    if candidate is None or candidate.content is None:
        # Blocked or empty output: the conversation answers with a safe retry prompt.
        return LLMResponse(content=None, provider=provider)
    parts = candidate.content.parts or []
    text = "".join(p.text for p in parts if p.text and not p.thought)
    calls = tuple(
        ToolCall(
            id=p.function_call.id or f"call_{uuid.uuid4().hex[:16]}",
            name=p.function_call.name or "",
            arguments=json.dumps(p.function_call.args or {}),
        )
        for p in parts
        if p.function_call is not None
    )
    return LLMResponse(
        content=text or None,
        tool_calls=calls,
        provider=provider,
        provider_state=candidate.content,
        usage=gemini_usage(response),
    )


def gemini_usage(response: types.GenerateContentResponse) -> dict[str, int] | None:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None
    usage = {
        "input": getattr(meta, "prompt_token_count", None),
        "output": getattr(meta, "candidates_token_count", None),
        "reasoning": getattr(meta, "thoughts_token_count", None),
    }
    return {k: int(v) for k, v in usage.items() if isinstance(v, int)} or None


def _retry_delay(exc: errors.APIError) -> float | None:
    body = exc.details if isinstance(exc.details, dict) else {}
    for detail in (body.get("error") or {}).get("details") or []:
        match = _RETRY_DELAY.match(str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None
