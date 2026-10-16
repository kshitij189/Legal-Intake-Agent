"""Sarvam adapter: request shape, response parsing and failure mapping (fake HTTP transport)."""

import json
from typing import Any

import httpx
import pytest

from app.agent.llm_client import FallbackLLMClient, LLMUnavailable, build_llm_client
from app.agent.llm_sarvam import SKIP_SECONDS, SarvamLLMClient, parse_response
from app.config import Settings


def client_for(handler: Any, *, max_retries: int = 0, budget: float = 0.0) -> SarvamLLMClient:
    return SarvamLLMClient(
        api_key="test-key",
        model="sarvam-105b-conversations",
        timeout_seconds=5,
        max_retries=max_retries,
        rate_limit_max_wait_seconds=budget,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=body)


TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]


def test_request_is_openai_shaped_and_private_keys_are_stripped() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return ok({"choices": [{"message": {"content": "Hello"}}]})

    reply = client_for(handler).complete(
        [{"role": "user", "content": "hi", "_provider_state": {"x": 1}}], TOOLS
    )
    assert reply.content == "Hello" and reply.provider == "sarvam:sarvam-105b-conversations"
    assert seen["url"] == "https://api.sarvam.ai/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer test-key"
    assert seen["headers"]["api-subscription-key"] == "test-key"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["body"]["max_tokens"] == 2048
    assert seen["body"]["tools"] == TOOLS and seen["body"]["tool_choice"] == "auto"


def test_no_tools_means_no_tool_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "tools" not in body and "tool_choice" not in body
        return ok({"choices": [{"message": {"content": "x"}}]})

    client_for(handler).complete([{"role": "user", "content": "hi"}], [])


def test_tool_calls_and_usage_are_parsed() -> None:
    data = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "save", "arguments": '{"a": 1}'}},
                        {"function": {"name": "dict_args", "arguments": {"b": 2}}},
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 2100, "completion_tokens": 40},
    }
    response = parse_response(data, "sarvam:m")
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("c1", "save", '{"a": 1}'),
        ("call_1", "dict_args", '{"b": 2}'),
    ]
    assert response.usage == {"input": 2100, "output": 40}


def test_inline_thinking_is_not_spoken() -> None:
    data = {"choices": [{"message": {"content": "<think>plan…</think>Thank you, Priya."}}]}
    assert parse_response(data, "p").content == "Thank you, Priya."
    assert (
        parse_response({"choices": [{"message": {"content": "<think>x</think>"}}]}, "p").content
        is None
    )
    assert parse_response({"choices": []}, "p").content is None


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(402, text="payment required"), "credits_exhausted"),
        (httpx.Response(429, text='{"error": "Insufficient credits"}'), "credits_exhausted"),
        (httpx.Response(400, text="quota exceeded for this key"), "credits_exhausted"),
        (httpx.Response(401, text="bad key"), "auth_failed"),
        (httpx.Response(403, text="forbidden"), "auth_failed"),
    ],
)
def test_exhausted_credits_and_bad_keys_make_the_chain_skip_sarvam(
    response: httpx.Response, reason: str
) -> None:
    with pytest.raises(LLMUnavailable) as failure:
        client_for(lambda _: response).complete([{"role": "user", "content": "hi"}], [])
    assert failure.value.reason == reason
    assert failure.value.retry_after == SKIP_SECONDS  # > the chain's cooldown threshold


def test_plain_rate_limit_waits_only_within_the_budget() -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="slow down", headers={"retry-after": "2"})
        return ok({"choices": [{"message": {"content": "back"}}]})

    assert (
        client_for(handler, budget=30).complete([{"role": "user", "content": "x"}], []).content
        == "back"
    )
    with pytest.raises(LLMUnavailable, match="rate_limited"):
        client_for(
            lambda _: httpx.Response(429, text="slow", headers={"retry-after": "2"})
        ).complete([{"role": "user", "content": "x"}], [])


def test_server_errors_retry_only_when_allowed() -> None:
    attempts: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, text="down")

    with pytest.raises(LLMUnavailable, match="http_503"):
        client_for(handler, max_retries=0).complete([{"role": "user", "content": "x"}], [])
    assert len(attempts) == 1
    attempts.clear()
    with pytest.raises(LLMUnavailable, match="http_503"):
        client_for(handler, max_retries=2).complete([{"role": "user", "content": "x"}], [])
    assert len(attempts) == 3


def test_timeouts_and_connection_errors() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    def refused(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMUnavailable, match="timeout"):
        client_for(timeout).complete([{"role": "user", "content": "x"}], [])
    with pytest.raises(LLMUnavailable, match="connection_error"):
        client_for(refused).complete([{"role": "user", "content": "x"}], [])


# --- the chain ---------------------------------------------------------------------------


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/x")
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "SARVAM_API_KEY", "LLM_PROVIDERS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_sarvam_joins_the_chain_only_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = "sarvam,gemini:gemini-3.5-flash-lite"
    with_key = build_llm_client(
        settings(monkeypatch, LLM_PROVIDERS=chain, SARVAM_API_KEY="k", GEMINI_API_KEY="g")
    )
    assert isinstance(with_key, FallbackLLMClient)
    assert with_key.provider_names == [
        "sarvam:sarvam-105b-conversations",
        "gemini:gemini-3.5-flash-lite",
    ]
    without = build_llm_client(settings(monkeypatch, LLM_PROVIDERS=chain, GEMINI_API_KEY="g"))
    assert isinstance(without, FallbackLLMClient)
    assert without.provider_names == ["gemini:gemini-3.5-flash-lite"]
    placeholder = build_llm_client(
        settings(monkeypatch, LLM_PROVIDERS=chain, SARVAM_API_KEY="replace-me", GEMINI_API_KEY="g")
    )
    assert isinstance(placeholder, FallbackLLMClient)
    assert placeholder.provider_names == ["gemini:gemini-3.5-flash-lite"]


def test_explicit_model_and_only_the_last_entry_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_llm_client(
        settings(
            monkeypatch,
            LLM_PROVIDERS="sarvam:sarvam-105b,sarvam",
            SARVAM_API_KEY="k",
            LLM_MAX_RETRIES="2",
        )
    )
    assert isinstance(client, FallbackLLMClient)
    first, last = (c for _, c in client._entries)
    assert client.provider_names == ["sarvam:sarvam-105b", "sarvam:sarvam-105b-conversations"]
    assert (first._max_retries, last._max_retries) == (0, 2)  # type: ignore[attr-defined]


def test_chain_skips_a_sarvam_with_exhausted_credits_and_keeps_answering() -> None:
    """The user-facing guarantee: when the one-time credits run out, calls continue on the next
    model, and Sarvam is not asked again until the skip window ends."""
    calls = {"sarvam": 0}

    def exhausted(_: httpx.Request) -> httpx.Response:
        calls["sarvam"] += 1
        return httpx.Response(402, text="insufficient credits")

    class Next:
        def complete(self, messages: Any, tools: Any) -> Any:
            from app.agent.llm_client import LLMResponse

            return LLMResponse("from the next model", provider="gemini:x")

    now = [1000.0]
    chain = FallbackLLMClient(
        [("sarvam:sarvam-105b-conversations", client_for(exhausted)), ("gemini:x", Next())],
        clock=lambda: now[0],
    )
    for _ in range(3):
        assert (
            chain.complete([{"role": "user", "content": "hi"}], []).content == "from the next model"
        )
    assert calls["sarvam"] == 1  # skipped after the first failure
    now[0] += SKIP_SECONDS + 1
    chain.complete([{"role": "user", "content": "hi"}], [])
    assert calls["sarvam"] == 2  # re-checked after the window (e.g. credits topped up)
