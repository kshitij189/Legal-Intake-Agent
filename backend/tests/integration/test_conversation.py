"""Conversation loop with a scripted model against the real database."""

from collections.abc import Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.conversation import (
    FAILURE_FLAGGED,
    FAILURE_NOT_FLAGGED,
    RETRY_PROMPT,
    ConversationEnded,
    IntakeConversation,
)
from app.agent.llm_client import PROVIDER_STATE_KEY, LLMResponse, LLMUnavailable
from app.domain import intake_service
from app.models import CallSession, Escalation, IntakeRecord
from app.models.enums import (
    CallSessionStatus,
    EscalationReason,
    EscalationStatus,
    FieldStatus,
    IntakeStatus,
)
from tests.fakes import ScriptedLLM, Step, call, say, tools
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm

Demo = tuple[sessionmaker[Session], SeededFirm]


def conversation(demo: Demo, steps: Sequence[Step]) -> tuple[IntakeConversation, ScriptedLLM]:
    sessions, firm = demo
    llm = ScriptedLLM(steps)
    convo = IntakeConversation(llm=llm, sessions=sessions, firm_id=firm.firm_id, clock=lambda: NOW)
    return convo, llm


def stored(sessions: sessionmaker[Session]) -> tuple[CallSession, IntakeRecord | None]:
    with sessions() as s:
        call_session = s.scalars(select(CallSession)).one()
        intake = s.scalars(select(IntakeRecord)).one_or_none()
        return call_session, intake


def test_greeting_discloses_role_and_session_starts(committed_demo: Demo) -> None:
    convo, _ = conversation(committed_demo, [])
    opening = convo.start()
    assert "Northstar Legal" in opening and "virtual intake assistant" in opening
    assert "can't give legal advice" in opening
    call_session, intake = stored(committed_demo[0])
    assert call_session.status is CallSessionStatus.ACTIVE and intake is None


def test_tool_calls_run_in_order_and_results_feed_back(committed_demo: Demo) -> None:
    convo, llm = conversation(
        committed_demo,
        [
            tools(
                call("create_draft_intake", "c1"),
                call(
                    "save_intake_answer",
                    "c2",
                    field_key="caller_name",
                    status="REPORTED_BY_CALLER",
                    value="Priya Sharma",
                ),
                call(
                    "save_intake_answer",
                    "c3",
                    field_key="reported_deadline",
                    status="REPORTED_BY_CALLER",
                    value="2026-10-13",
                ),
            ),
            say("Thank you, Priya. I've flagged the deadline for the team's prompt review."),
        ],
    )
    convo.start()
    reply = convo.respond("I'm Priya Sharma and my notice says the deadline is tomorrow.")
    assert reply.startswith("Thank you, Priya")

    # The second model request carries every tool result, in order and tied to its call id.
    results = llm.tool_results()
    assert [r["ok"] for r in results] == [True, True, True]
    tool_messages = [m for m in llm.requests[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2", "c3"]
    assert results[2]["data"]["escalations_for_human_review"][0]["rule_id"] == "imminent_deadline"
    assert [t.tool for t in convo.trace] == [
        "create_draft_intake",
        "save_intake_answer",
        "save_intake_answer",
    ]

    _, intake = stored(committed_demo[0])
    assert intake is not None
    fields = intake_service.intake_fields(intake)
    assert fields["caller_name"].value == "Priya Sharma"
    assert fields["reported_deadline"].status is FieldStatus.REPORTED_BY_CALLER


def test_model_errors_are_returned_to_the_model_not_hidden(committed_demo: Demo) -> None:
    convo, llm = conversation(
        committed_demo,
        [
            tools(
                call(
                    "save_intake_answer",
                    field_key="caller_name",
                    status="VERIFIED_BY_SYSTEM",
                    value="Priya",
                )
            ),
            say("Could you tell me your name?"),
        ],
    )
    convo.start()
    assert convo.respond("Hi") == "Could you tell me your name?"
    [result] = llm.tool_results()
    assert result["ok"] is False and result["error"]["code"] == "INVALID_ARGUMENTS"


def test_llm_outage_after_intake_keeps_state_and_queues_review(committed_demo: Demo) -> None:
    convo, _ = conversation(
        committed_demo,
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
            LLMUnavailable("timeout"),
        ],
    )
    convo.start()
    assert convo.respond("I'm Priya Sharma.") == FAILURE_FLAGGED
    assert convo.ended

    call_session, intake = stored(committed_demo[0])
    assert call_session.status is CallSessionStatus.FAILED
    assert call_session.end_reason == "llm_unavailable:timeout"
    assert intake is not None and intake.status is IntakeStatus.INCOMPLETE
    assert intake_service.intake_fields(intake)["caller_name"].value == "Priya Sharma"
    with committed_demo[0]() as s:
        escalation = s.scalars(select(Escalation)).one()
    assert escalation.reason_code is EscalationReason.UNABLE_TO_COMPLETE
    assert escalation.status is EscalationStatus.PENDING
    with pytest.raises(ConversationEnded):
        convo.respond("hello?")


def test_llm_outage_before_any_intake_does_not_claim_anything(committed_demo: Demo) -> None:
    convo, _ = conversation(committed_demo, [LLMUnavailable("rate_limited")])
    convo.start()
    assert convo.respond("Hello") == FAILURE_NOT_FLAGGED
    call_session, intake = stored(committed_demo[0])
    assert call_session.status is CallSessionStatus.FAILED and intake is None


def test_disconnect_marks_unfinished_intake_incomplete(committed_demo: Demo) -> None:
    convo, _ = conversation(
        committed_demo,
        [tools(call("create_draft_intake")), say("What's your name?")],
    )
    convo.start()
    convo.respond("I need help with my landlord.")
    assert convo.end() is CallSessionStatus.DISCONNECTED
    call_session, intake = stored(committed_demo[0])
    assert call_session.ended_at == NOW
    assert intake is not None and intake.status is IntakeStatus.INCOMPLETE
    with pytest.raises(ConversationEnded):
        convo.end()


def test_finalized_conversation_completes(committed_demo: Demo) -> None:
    convo, _ = conversation(
        committed_demo,
        [
            tools(
                call("create_draft_intake"),
                call(
                    "save_intake_answer",
                    field_key="matter_summary",
                    status="REPORTED_BY_CALLER",
                    value="Tenancy issue.",
                ),
            ),
            tools(call("finalize_intake_summary", summary="Caller asked about a tenancy issue.")),
            say("Thank you, the team will review your enquiry."),
        ],
    )
    convo.start()
    convo.respond("That's all, thanks.")
    assert convo.end() is CallSessionStatus.COMPLETED
    _, intake = stored(committed_demo[0])
    assert intake is not None and intake.status is IntakeStatus.PENDING_REVIEW


def test_tool_round_limit_returns_safe_prompt(committed_demo: Demo) -> None:
    sessions, firm = committed_demo
    looping = [tools(call("get_intake_questions")) for _ in range(3)]
    convo = IntakeConversation(
        llm=ScriptedLLM(looping),
        sessions=sessions,
        firm_id=firm.firm_id,
        clock=lambda: NOW,
        max_tool_rounds=3,
    )
    convo.start()
    assert convo.respond("Hello") == RETRY_PROMPT
    assert not convo.ended  # the caller can simply try again


def test_empty_model_reply_falls_back_to_retry_prompt(committed_demo: Demo) -> None:
    convo, _ = conversation(committed_demo, [say("   ")])
    convo.start()
    assert convo.respond("Hello") == RETRY_PROMPT


def test_provider_state_is_kept_for_the_next_request(committed_demo: Demo) -> None:
    state = object()  # opaque, e.g. Gemini content carrying thought signatures
    convo, llm = conversation(
        committed_demo,
        [
            LLMResponse(
                content=None,
                tool_calls=(call("create_draft_intake", "c1"),),
                provider="gemini",
                provider_state=state,
            ),
            LLMResponse(content="Thanks!", provider="groq"),
        ],
    )
    convo.start()
    assert convo.respond("Hello") == "Thanks!"
    assert convo.providers_used == ["gemini", "groq"]
    assistant = next(m for m in llm.requests[1] if m.get("tool_calls"))
    assert assistant[PROVIDER_STATE_KEY] == {"gemini": state}
