"""A traced conversation (real tools + DB): the observation tree a reviewer sees in Langfuse."""

import json
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agent.conversation import IntakeConversation
from app.agent.llm_client import FallbackLLMClient, LLMResponse
from app.observability.tracing import Tracer
from tests.fakes import ScriptedLLM, call, say, tools
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm
from tests.unit.test_tracing import FakeClient, merged

Demo = tuple[sessionmaker[Session], SeededFirm]


@pytest.fixture
def traced(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeClient]:
    client = FakeClient()
    tracer = Tracer(client, capture_content=False)
    for module in ("app.agent.llm_client", "app.agent.conversation", "app.tools.executor"):
        monkeypatch.setattr(f"{module}.get_tracer", lambda: tracer)
    yield client


def test_turns_generations_tools_and_guard_are_traced(
    committed_demo: Demo, traced: FakeClient
) -> None:
    sessions, firm = committed_demo
    model = ScriptedLLM(
        [
            tools(
                call("create_draft_intake"),
                call(
                    "save_intake_answer",
                    field_key="caller_name",
                    status="REPORTED_BY_CALLER",
                    value="Priya Sharma",
                ),
            ),
            LLMResponse("Thank you, Priya. What phone number?", usage={"input": 900, "output": 9}),
            say("Your consultation is booked for tomorrow."),  # false claim -> guard
            say("No consultation is booked yet. Would you like one?"),
        ]
    )
    convo = IntakeConversation(
        llm=FallbackLLMClient([("fake:model", model)]),
        sessions=sessions,
        firm_id=firm.firm_id,
        clock=lambda: NOW,
        trace_tags=("test",),
    )
    convo.start()
    convo.respond("Hi, I'm Priya Sharma.")
    convo.respond("Is my consultation booked?")
    convo.end()

    turns = [merged(o) for o in traced.named("intake.turn")]
    assert [t["as_type"] for t in turns] == ["agent", "agent"]
    assert turns[0]["metadata"]["tools"] == ["create_draft_intake", "save_intake_answer"]
    assert len(turns[0]["metadata"]["models"]) == 2  # tool round + reply
    generations = [merged(o) for o in traced.observations if o["as_type"] == "generation"]
    assert len(generations) == 4 and generations[1]["usage_details"] == {"input": 900, "output": 9}
    tool_obs = [merged(o) for o in traced.observations if o["as_type"] == "tool"]
    assert [(t["name"], t["metadata"]["ok"]) for t in tool_obs] == [
        ("tool create_draft_intake", True),
        ("tool save_intake_answer", True),
    ]
    [guard] = [merged(o) for o in traced.named("reply_guard")]
    assert guard["output"] == {"violations": ["false_booking_confirmation"]}
    assert guard["level"] == "WARNING"
    # Content capture is off: no caller statement, reply or tool argument leaves the process.
    sent = json.dumps(traced.observations, default=str)
    for secret in ("Priya", "booked for tomorrow", "Is my consultation"):
        assert secret not in sent
    assert traced.flushed == 0  # export is asynchronous; ending a call never blocks on it
