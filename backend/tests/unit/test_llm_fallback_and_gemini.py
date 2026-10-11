import json
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors, types

from app.agent.llm_client import (
    PROVIDER_STATE_KEY,
    FallbackLLMClient,
    LLMResponse,
    LLMUnavailable,
    build_llm_client,
)
from app.agent.llm_gemini import (
    FOREIGN_THOUGHT_SIGNATURE,
    GeminiLLMClient,
    from_gemini_response,
    to_function_declarations,
    to_gemini_contents,
)
from app.agent.llm_groq import GroqLLMClient
from app.config import Settings

G = "gemini:gemini-3.8-flash"


class Stub:
    def __init__(self, outcome: LLMResponse | LLMUnavailable) -> None:
        self.outcome = outcome
        self.calls = 0

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        self.calls += 1
        if isinstance(self.outcome, LLMUnavailable):
            raise self.outcome
        return self.outcome


# --- fallback chain -------------------------------------------------------------------------


def test_primary_answers_and_fallback_is_not_called() -> None:
    primary, fallback = Stub(LLMResponse("hi", provider="gemini")), Stub(LLMResponse("x"))
    chain = FallbackLLMClient([("gemini", primary), ("groq", fallback)])
    assert chain.complete([], []).content == "hi"
    assert (primary.calls, fallback.calls, chain.last_provider) == (1, 0, "gemini")


def test_falls_back_when_primary_is_unavailable() -> None:
    primary = Stub(LLMUnavailable("rate_limited"))
    fallback = Stub(LLMResponse("from groq", provider="groq"))
    chain = FallbackLLMClient([("gemini", primary), ("groq", fallback)])
    assert chain.complete([], []).content == "from groq"
    assert chain.last_provider == "groq"


def test_all_providers_failing_reports_every_reason() -> None:
    chain = FallbackLLMClient(
        [("gemini", Stub(LLMUnavailable("http_503"))), ("groq", Stub(LLMUnavailable("timeout")))]
    )
    with pytest.raises(LLMUnavailable) as caught:
        chain.complete([], [])
    assert caught.value.reason == "gemini:http_503; groq:timeout"


def settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/x")
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "LLM_PROVIDERS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


DEFAULT_CHAIN = [
    "gemini:gemini-3.8-flash",
    "gemini:gemini-3.5-flash-lite",
    "groq:openai/gpt-oss-120b",
    "groq:openai/gpt-oss-20b",
]


def test_default_chain_is_gemini_first_and_only_last_entry_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = build_llm_client(settings(monkeypatch, GEMINI_API_KEY="g", GROQ_API_KEY="q"))
    assert isinstance(client, FallbackLLMClient)
    assert client.provider_names == DEFAULT_CHAIN
    clients = [c for _, c in client._entries]
    assert [type(c) for c in clients] == [GeminiLLMClient] * 2 + [GroqLLMClient] * 2
    assert [c._rate_limit_budget for c in clients] == [0.0, 0.0, 0.0, 30.0]  # type: ignore[attr-defined]


def test_bare_entries_use_the_provider_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_llm_client(
        settings(monkeypatch, LLM_PROVIDERS="gemini, groq", GEMINI_API_KEY="g", GROQ_API_KEY="q")
    )
    assert isinstance(client, FallbackLLMClient)
    assert client.provider_names == ["gemini:gemini-3.8-flash", "groq:openai/gpt-oss-120b"]


def test_factory_skips_providers_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_llm_client(settings(monkeypatch, GROQ_API_KEY="q"))
    assert isinstance(client, FallbackLLMClient)
    assert client.provider_names == DEFAULT_CHAIN[2:]


def test_factory_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="openai"):
        build_llm_client(settings(monkeypatch, LLM_PROVIDERS="openai,groq", GROQ_API_KEY="q"))


def test_exhausted_quota_is_skipped_until_it_resets() -> None:
    now = [1000.0]
    daily = Stub(LLMUnavailable("rate_limited", retry_after=34_000))
    backup = Stub(LLMResponse("ok", provider="groq:m"))
    chain = FallbackLLMClient([("gemini:a", daily), ("groq:m", backup)], clock=lambda: now[0])

    chain.complete([], [])
    chain.complete([], [])
    assert daily.calls == 1  # second call skipped the exhausted model entirely
    now[0] += 34_001
    chain.complete([], [])
    assert daily.calls == 2  # retried once the quota window has passed


def test_short_rate_limits_do_not_trigger_cooldown() -> None:
    flaky = Stub(LLMUnavailable("rate_limited", retry_after=8))
    chain = FallbackLLMClient(
        [("gemini:a", flaky), ("groq:m", Stub(LLMResponse("ok")))], clock=lambda: 0.0
    )
    chain.complete([], [])
    chain.complete([], [])
    assert flaky.calls == 2


# --- Gemini conversion ----------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_intake_answer",
            "description": "Save one answer.",
            "parameters": {
                "type": "object",
                "properties": {"field_key": {"type": "string", "enum": ["phone"]}},
                "required": ["field_key"],
                "additionalProperties": False,
            },
        },
    }
]


def history(assistant_extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "create_draft_intake", "arguments": "{}"},
            },
            {
                "id": "c2",
                "type": "function",
                "function": {"name": "save_intake_answer", "arguments": '{"field_key": "phone"}'},
            },
        ],
        **(assistant_extra or {}),
    }
    return [
        {"role": "system", "content": "be helpful"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "My number is 98765 43210"},
        assistant,
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"ok": True})},
        {"role": "tool", "tool_call_id": "c2", "content": json.dumps({"ok": False})},
    ]


def test_history_from_another_provider_gets_placeholder_signature() -> None:
    system, contents = to_gemini_contents(history(), own_key=G)
    assert system == "be helpful"
    assert [c.role for c in contents] == ["model", "user", "model", "user"]
    calls = contents[2].parts
    assert calls is not None
    assert [p.function_call.name for p in calls if p.function_call] == [
        "create_draft_intake",
        "save_intake_answer",
    ]
    assert calls[0].thought_signature == FOREIGN_THOUGHT_SIGNATURE
    assert calls[1].thought_signature is None
    results = contents[3].parts  # both tool results grouped in one turn, matched by call id
    assert results is not None
    assert [
        (p.function_response.name, p.function_response.response)
        for p in results
        if p.function_response
    ] == [
        ("create_draft_intake", {"result": {"ok": True}}),
        ("save_intake_answer", {"result": {"ok": False}}),
    ]


def test_geminis_own_turn_is_replayed_verbatim_with_its_signature() -> None:
    own = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(name="create_draft_intake", args={}),
                thought_signature=b"real-signature",
            )
        ],
    )
    _, contents = to_gemini_contents(history({PROVIDER_STATE_KEY: {G: own}}), own_key=G)
    assert contents[2] is own


def test_function_declarations_use_raw_json_schema() -> None:
    [declaration] = to_function_declarations(TOOLS)
    assert declaration.name == "save_intake_answer"
    assert declaration.parameters_json_schema == TOOLS[0]["function"]["parameters"]


def response_with(*parts: types.Part) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))]
    )


def test_parses_tool_calls_text_and_keeps_state() -> None:
    response = response_with(
        types.Part(text="thinking...", thought=True),
        types.Part(
            function_call=types.FunctionCall(
                id="fc1", name="save_intake_answer", args={"field_key": "phone"}
            ),
            thought_signature=b"sig",
        ),
    )
    parsed = from_gemini_response(response, provider=G)
    assert parsed.provider == G and parsed.content is None  # thoughts are not replies
    [call] = parsed.tool_calls
    assert (call.id, call.name, json.loads(call.arguments)) == (
        "fc1",
        "save_intake_answer",
        {"field_key": "phone"},
    )
    assert parsed.provider_state.parts[1].thought_signature == b"sig"


def test_missing_call_ids_are_generated_and_empty_output_is_safe() -> None:
    parsed = from_gemini_response(
        response_with(types.Part(function_call=types.FunctionCall(name="get_intake_status"))),
        provider=G,
    )
    assert parsed.tool_calls[0].id.startswith("call_")
    assert from_gemini_response(types.GenerateContentResponse(candidates=[]), G).content is None


class FakeGenai:
    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gemini(
    fake: FakeGenai, budget: float = 0.0, slept: list[float] | None = None
) -> GeminiLLMClient:
    return GeminiLLMClient(
        api_key="unused",
        model="gemini-3.8-flash",
        timeout_seconds=5,
        max_retries=0,
        rate_limit_max_wait_seconds=budget,
        client=fake,
        sleep=(slept if slept is not None else []).append,
    )


def api_error(code: int, retry_delay: str | None = None) -> errors.APIError:
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    body = {"error": {"code": code, "message": "x", "details": details if retry_delay else []}}
    return errors.APIError(code, body)


def test_gemini_request_shape() -> None:
    fake = FakeGenai(response_with(types.Part(text="Hello there")))
    reply = gemini(fake).complete(history(), TOOLS)
    assert reply.content == "Hello there"
    request = fake.requests[0]
    assert request["model"] == "gemini-3.8-flash"
    config = request["config"]
    assert config.system_instruction == "be helpful"
    assert config.automatic_function_calling.disable is True
    assert config.tools[0].function_declarations[0].name == "save_intake_answer"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (api_error(503), "http_503"),
        (api_error(401), "http_401"),
        (api_error(429), "rate_limited"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("down"), "connection_error"),
    ],
)
def test_gemini_errors_map_to_llm_unavailable(error: Exception, reason: str) -> None:
    with pytest.raises(LLMUnavailable) as caught:
        gemini(FakeGenai(error)).complete([], [])
    assert caught.value.reason == reason


def test_gemini_rate_limit_honours_retry_delay_within_budget() -> None:
    slept: list[float] = []
    fake = FakeGenai(api_error(429, "12s"), response_with(types.Part(text="ok")))
    assert gemini(fake, budget=30, slept=slept).complete([], []).content == "ok"
    assert slept == [12.0]
