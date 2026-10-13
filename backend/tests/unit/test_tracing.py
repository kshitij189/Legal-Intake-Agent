"""Langfuse tracing adapter: no-op when off, content redaction, never breaks the caller."""

import contextlib
import json
from collections.abc import Iterator
from typing import Any

import pytest

from app.agent.llm_client import FallbackLLMClient, LLMResponse, LLMUnavailable
from app.config import Settings
from app.observability.tracing import Tracer, build_tracer


class FakeObservation:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    def update(self, **fields: Any) -> None:
        self.record.setdefault("updates", []).append(fields)


class FakeClient:
    """Records observations the way the instrumented code creates them."""

    def __init__(self, fail: bool = False) -> None:
        self.observations: list[dict[str, Any]] = []
        self.flushed = 0
        self.fail = fail

    @contextlib.contextmanager
    def start_as_current_observation(self, **fields: Any) -> Iterator[FakeObservation]:
        if self.fail:
            raise RuntimeError("langfuse down")
        record = dict(fields)
        self.observations.append(record)
        yield FakeObservation(record)

    def flush(self) -> None:
        self.flushed += 1

    def named(self, name: str) -> list[dict[str, Any]]:
        return [o for o in self.observations if o["name"] == name]


def merged(record: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in record.items() if k != "updates"}
    for update in record.get("updates", []):
        out.update(update)
    return out


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeClient]:
    client = FakeClient()
    tracer = Tracer(client, capture_content=False)
    for module in ("app.agent.llm_client", "app.agent.conversation", "app.tools.executor"):
        monkeypatch.setattr(f"{module}.get_tracer", lambda: tracer)
    yield client


def test_disabled_without_flag_or_keys() -> None:
    def settings(**values: object) -> Settings:
        return Settings(_env_file=None, database_url="postgresql://x/y", **values)  # type: ignore[call-arg]

    off = build_tracer(settings(langfuse_enabled=False, langfuse_public_key="pk"))
    assert not off.enabled
    no_keys = build_tracer(settings(langfuse_enabled=True))
    assert not no_keys.enabled
    with off.observe("x") as observation:
        observation.update(output="ignored")  # no-op, no error
    off.flush()


def test_content_is_redacted_unless_capture_is_on() -> None:
    assert Tracer(FakeClient()).content("Priya Sharma, 9876543210") == {
        "redacted": True,
        "chars": 24,
    }
    assert Tracer(FakeClient(), capture_content=True).content("text") == "text"


def test_sdk_failures_never_reach_the_caller() -> None:
    tracer = Tracer(FakeClient(fail=True))
    with tracer.observe("x") as observation:
        observation.update(output="still fine")
    with tracer.session("s", trace_name="t", tags=[], metadata={}):
        pass


def test_exceptions_are_marked_and_reraised() -> None:
    client = FakeClient()
    with pytest.raises(ValueError), Tracer(client).observe("x"):
        raise ValueError("boom")
    assert merged(client.observations[0])["level"] == "ERROR"


class Scripted:
    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self.responses = list(responses)

    def complete(self, messages: Any, tools: Any) -> LLMResponse:
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_fallback_chain_records_one_generation_per_attempt(fake: FakeClient) -> None:
    chain = FallbackLLMClient(
        [
            ("gemini:a", Scripted(LLMUnavailable("rate_limited"))),
            (
                "groq:b",
                Scripted(LLMResponse("hi", provider="groq:b", usage={"input": 12, "output": 3})),
            ),
        ]
    )
    chain.complete([{"role": "user", "content": "Priya Sharma"}], [])
    failed, answered = (merged(o) for o in fake.observations)
    assert (failed["as_type"], failed["model"], failed["level"]) == (
        "generation",
        "gemini:a",
        "WARNING",
    )
    assert failed["status_message"] == "rate_limited"
    assert answered["usage_details"] == {"input": 12, "output": 3}
    assert "Priya Sharma" not in json.dumps(answered, default=str)  # capture is off


def test_generation_input_is_only_what_is_new() -> None:
    from app.agent.llm_client import new_messages

    history = [
        {"role": "system", "content": "long prompt"},
        {"role": "assistant", "content": "Hello", "_provider_state": {"x": 1}},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "content": "{}"},
    ]
    assert new_messages(history) == [{"role": "tool", "content": "{}"}]
    assert new_messages(history[:3]) == [{"role": "user", "content": "Hi"}]
