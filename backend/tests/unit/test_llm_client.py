from types import SimpleNamespace
from typing import Any

import groq
import httpx
import pytest

from app.agent.llm_client import LLMUnavailable, build_llm_client
from app.agent.llm_groq import GroqLLMClient
from app.config import Settings

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def status_error(cls: type[groq.APIStatusError], status: int, code: str) -> groq.APIStatusError:
    body = {"error": {"code": code, "message": code}}
    return cls(code, response=httpx.Response(status, request=REQUEST), body=body)


def completion(content: str | None = None, calls: list[tuple[str, str, str]] | None = None) -> Any:
    tool_calls = [
        SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=a))
        for i, n, a in calls or []
    ] or None
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeGroq:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.kwargs: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client(fake: FakeGroq) -> GroqLLMClient:
    return GroqLLMClient(
        api_key="unused", model="openai/gpt-oss-120b", timeout_seconds=5, max_retries=0, client=fake
    )


def test_parses_tool_calls_and_sends_tools() -> None:
    fake = FakeGroq(completion(calls=[("c1", "create_draft_intake", "{}")]))
    tools = [{"type": "function", "function": {"name": "create_draft_intake"}}]
    response = client(fake).complete([{"role": "user", "content": "hi"}], tools)
    assert response.content is None
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("c1", "create_draft_intake", "{}")
    ]
    sent = fake.kwargs[0]
    assert sent["model"] == "openai/gpt-oss-120b"
    assert sent["tools"] == tools and sent["tool_choice"] == "auto"


def test_plain_text_reply() -> None:
    response = client(FakeGroq(completion("Hello"))).complete([], [])
    assert (response.content, response.tool_calls) == ("Hello", ())


def test_tool_use_failed_is_retried_once() -> None:
    fake = FakeGroq(
        status_error(groq.BadRequestError, 400, "tool_use_failed"), completion("recovered")
    )
    assert client(fake).complete([], []).content == "recovered"
    assert len(fake.kwargs) == 2


def test_repeated_tool_use_failure_becomes_unavailable() -> None:
    error = status_error(groq.BadRequestError, 400, "tool_use_failed")
    with pytest.raises(LLMUnavailable, match="tool_use_failed"):
        client(FakeGroq(error, error)).complete([], [])


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (groq.APITimeoutError(request=REQUEST), "timeout"),
        (groq.APIConnectionError(request=REQUEST), "connection_error"),
        (status_error(groq.RateLimitError, 429, "rate_limit_exceeded"), "rate_limited"),
        (status_error(groq.AuthenticationError, 401, "invalid_api_key"), "http_401"),
        (status_error(groq.InternalServerError, 503, "unavailable"), "http_503"),
    ],
)
def test_provider_errors_map_to_llm_unavailable(error: Exception, reason: str) -> None:
    with pytest.raises(LLMUnavailable) as caught:
        client(FakeGroq(error)).complete([], [])
    assert caught.value.reason == reason


def rate_limited(retry_after: str | None) -> groq.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, request=REQUEST, headers=headers)
    return groq.RateLimitError("rate limited", response=response, body={"error": {}})


def patient_client(fake: FakeGroq, budget: float, slept: list[float]) -> GroqLLMClient:
    return GroqLLMClient(
        api_key="unused",
        model="m",
        timeout_seconds=5,
        max_retries=0,
        rate_limit_max_wait_seconds=budget,
        client=fake,
        sleep=slept.append,
    )


def test_rate_limit_waits_as_instructed_within_budget() -> None:
    slept: list[float] = []
    fake = FakeGroq(rate_limited("7"), rate_limited(None), completion("ok"))
    assert patient_client(fake, 30, slept).complete([], []).content == "ok"
    assert slept == [7.0, 5.0]  # retry-after honoured; default delay without the header


def test_rate_limit_beyond_budget_gives_up() -> None:
    slept: list[float] = []
    fake = FakeGroq(rate_limited("20"), rate_limited("20"))
    with pytest.raises(LLMUnavailable, match="rate_limited"):
        patient_client(fake, 30, slept).complete([], [])
    assert slept == [20.0]


def test_missing_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/x")
    monkeypatch.setenv("GROQ_API_KEY", "replace-me")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="no API key configured for any of: gemini, groq"):
        build_llm_client(Settings(_env_file=None))  # type: ignore[call-arg]
