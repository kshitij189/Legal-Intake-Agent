"""Transcript-mode scenarios (blueprint §13) with a scripted model and DB assertions.

The model's turns are fixed, so these tests check what the *system* guarantees whatever the
model does: provenance, refusals, ambiguity, corrections, escalation, handoff and claim checks.
Live-model behaviour is measured separately by scripts/run_scripted_conversations.py.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.conversation import IntakeConversation
from app.agent.llm_client import LLMResponse, LLMUnavailable
from app.agent.reply_guard import FALSE_BOOKING, UNCONFIRMED_ACCEPTANCE
from app.domain import escalation_service, intake_service
from app.models import AuditEvent, Escalation, IntakeRecord
from app.models.enums import AuditOutcome, EscalationStatus, FieldStatus, IntakeStatus
from tests.fakes import ScriptedLLM, Step, call, say, tools
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm

Demo = tuple[sessionmaker[Session], SeededFirm]
TOMORROW = "2026-10-13"


def run(demo: Demo, steps: Sequence[Step], *turns: str) -> tuple[IntakeConversation, ScriptedLLM]:
    sessions, firm = demo
    llm = ScriptedLLM(steps)
    convo = IntakeConversation(llm=llm, sessions=sessions, firm_id=firm.firm_id, clock=lambda: NOW)
    convo.start()
    for turn in turns:
        convo.respond(turn)
    return convo, llm


def save(key: str, value: str | None, status: str = "REPORTED_BY_CALLER", **extra: Any):  # type: ignore[no-untyped-def]
    return call(
        "save_intake_answer", f"save_{key}", field_key=key, status=status, value=value, **extra
    )


def the_intake(demo: Demo) -> IntakeRecord:
    with demo[0]() as s:
        return s.scalars(select(IntakeRecord)).one()


def fields(demo: Demo) -> dict[str, Any]:
    return intake_service.intake_fields(the_intake(demo))


def tool_results(convo: IntakeConversation) -> list[dict[str, Any]]:
    import json

    return [json.loads(t.result) for t in convo.trace]


def test_normal_enquiry_reaches_review_with_outstanding_items(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            tools(
                call("create_draft_intake"),
                save("caller_name", "Priya Sharma"),
                save("matter_category", "EMPLOYMENT"),
                save("matter_summary", "Employer withheld two months of salary."),
            ),
            say("Thank you. What phone number can the firm reach you on?"),
            tools(save("phone", "98765 43210"), save("preferred_contact_method", "PHONE")),
            say("Is there anything else?"),
            tools(
                call(
                    "finalize_intake_summary",
                    summary="Caller reports two months of withheld salary; reach on 98765 43210.",
                )
            ),
            say("Thank you, the team will review your enquiry."),
        ],
        "I'm Priya Sharma, my employer withheld two months of salary.",
        "98765 43210, phone is best.",
        "That's all.",
    )
    assert convo.state().next_action == "INTAKE_SUBMITTED_CLOSE_POLITELY"
    intake = the_intake(committed_demo)
    assert intake.status is IntakeStatus.PENDING_REVIEW
    assert intake.outstanding == {"missing": [], "declined": [], "needs_review": []}
    assert all(r["ok"] for r in tool_results(convo))


def test_state_is_injected_into_every_model_request(committed_demo: Demo) -> None:
    _, llm = run(
        committed_demo,
        [tools(call("create_draft_intake"), save("caller_name", "Priya")), say("Thanks!")],
        "I'm Priya.",
    )
    first, second = (r[0]["content"] for r in llm.requests)
    assert "no draft intake yet. Next action: CREATE_DRAFT_INTAKE" in first
    assert "caller_name='Priya' [REPORTED_BY_CALLER]" in second
    assert "next action: COLLECT_PHONE" in second


def test_reported_urgent_deadline_escalates_and_stays_unverified(committed_demo: Demo) -> None:
    run(
        committed_demo,
        [
            tools(call("create_draft_intake"), save("reported_deadline", TOMORROW)),
            say("I've flagged this for the team's prompt review."),
        ],
        "The notice says I must respond tomorrow.",
    )
    entry = fields(committed_demo)["reported_deadline"]
    assert (entry.value, entry.status) == (TOMORROW, FieldStatus.REPORTED_BY_CALLER)
    with committed_demo[0]() as s:
        escalation = s.scalars(select(Escalation)).one()
    assert (escalation.rule_id, escalation.status) == (
        "imminent_deadline",
        EscalationStatus.PENDING,
    )


def test_ambiguous_date_is_preserved_for_review_without_a_value(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            # A guessed date is rejected by validation; the vague answer is saved for review at
            # once, and only then does the agent ask its one clarifying question.
            tools(call("create_draft_intake"), save("reported_deadline", "end of next month")),
            tools(
                save(
                    "reported_deadline",
                    None,
                    "PENDING_HUMAN_REVIEW",
                    note="Caller said 'around the end of next month'; paper not available.",
                )
            ),
            say("I've noted that for the team. Do you know the exact date on the notice?"),
            say("No problem, the team will check the date with you."),
        ],
        "The date is around the end of next month.",
        "I really can't find the paper.",
    )
    entry = fields(committed_demo)["reported_deadline"]
    assert (entry.value, entry.status) == (None, FieldStatus.PENDING_HUMAN_REVIEW)
    assert "end of next month" in (entry.note or "")
    assert [r["ok"] for r in tool_results(convo)] == [True, False, True]
    with committed_demo[0]() as s:
        assert s.scalars(select(Escalation)).all() == []  # no value, so no deadline rule


def test_missing_phone_is_tracked_and_reported_at_finalization(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            tools(
                call("create_draft_intake"),
                save("caller_name", "Arjun Nair"),
                save("matter_category", "FAMILY"),
                save("matter_summary", "Custody question."),
            ),
            say("What phone number can we reach you on?"),
        ],
        "I'm Arjun, it's about custody.",
    )
    assert convo.state().next_action == "COLLECT_PHONE"
    assert "phone" in convo.state().missing_required
    convo.end(disconnected=True)
    assert the_intake(committed_demo).status is IntakeStatus.INCOMPLETE


def test_refusal_is_recorded_and_never_filled_in(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            tools(
                call("create_draft_intake"),
                save("caller_name", "Arjun Nair"),
                save("phone", None, "DECLINED_BY_CALLER", note="Prefers not to share"),
            ),
            say("Understood. Is there anything else?"),
            tools(
                save("matter_summary", "Custody question."),
                call("finalize_intake_summary", summary="Caller asked about child custody."),
            ),
            say("Thank you, the team will be in touch by email."),
        ],
        "I'd rather not give my phone number.",
        "That's all.",
    )
    entry = fields(committed_demo)["phone"]
    assert (entry.value, entry.status) == (None, FieldStatus.DECLINED_BY_CALLER)
    assert convo.state().declined == ("phone",)
    assert the_intake(committed_demo).outstanding == {
        "missing": ["preferred_contact_method", "matter_category"],
        "declined": ["phone"],
        "needs_review": [],
    }


def test_legal_question_is_recorded_for_the_lawyers(committed_demo: Demo) -> None:
    run(
        committed_demo,
        [
            tools(
                call("create_draft_intake"),
                save("caller_questions", "Should I refuse to leave the property?"),
            ),
            say("I can't give legal advice, but I've noted your question for the firm's lawyers."),
        ],
        "Should I just refuse to leave?",
    )
    entry = fields(committed_demo)["caller_questions"]
    assert entry.value == "Should I refuse to leave the property?"
    assert entry.status is FieldStatus.REPORTED_BY_CALLER


def test_unsupported_category_routes_to_review_without_slots(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            tools(
                call("create_draft_intake"),
                save("matter_category", "OTHER"),
                call("check_appointment_availability"),
            ),
            say("This falls outside our usual areas, so I've flagged it for the team."),
        ],
        "I need help registering a patent.",
    )
    availability = tool_results(convo)[-1]["data"]
    assert availability["slots"] == [] and "callback" in availability["note"]
    with committed_demo[0]() as s:
        escalation = s.scalars(select(Escalation)).one()
    assert escalation.rule_id == "outside_intake_workflow"


def test_interruption_and_correction_keep_a_clean_record(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            tools(call("create_draft_intake"), save("phone", "98765 43210")),
            say("Thanks. Which area does your enquiry relate to?"),
            # Caller interrupts with a correction; the model re-saves the field.
            tools(save("phone", "98765 43219")),
            say("Thanks, I've corrected your number. Which area does your enquiry relate to?"),
        ],
        "My number is 98765 43210.",
        "Sorry, wait - the last digit is 9, not 0.",
    )
    entry = fields(committed_demo)["phone"]
    assert entry.value == "9876543219"
    assert [(p.value, p.status) for p in entry.history] == [
        ("9876543210", FieldStatus.REPORTED_BY_CALLER)
    ]
    with committed_demo[0]() as s:
        saves = s.scalars(
            select(AuditEvent.details)
            .where(AuditEvent.action == "intake.field_saved")
            .order_by(AuditEvent.seq)
        ).all()
    assert [d["corrected"] for d in saves] == [False, True]
    assert convo.state().next_action == "COLLECT_CALLER_NAME"


def test_agent_cannot_overwrite_a_staff_verified_value(committed_demo: Demo) -> None:
    sessions, firm = committed_demo
    convo, _ = run(committed_demo, [tools(call("create_draft_intake")), say("Hi")], "Hello")
    intake_id = the_intake(committed_demo).id
    with sessions() as s, s.begin():
        intake_service.save_intake_answer(
            s,
            firm_id=firm.firm_id,
            intake_id=intake_id,
            actor=firm.coordinator,
            field_key="reported_deadline",
            status=FieldStatus.VERIFIED_BY_SYSTEM,
            value="2026-11-30",
            now=NOW,
        )
    convo.llm = ScriptedLLM([tools(save("reported_deadline", TOMORROW)), say("Noted.")])
    convo.respond("Actually the deadline is tomorrow.")
    [result] = tool_results(convo)[-1:]
    assert result["error"]["code"] == "PERMISSION_DENIED"
    entry = fields(committed_demo)["reported_deadline"]
    assert (entry.value, entry.status) == ("2026-11-30", FieldStatus.VERIFIED_BY_SYSTEM)


def test_finalize_rejects_invented_facts_and_empty_intakes(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            tools(
                call("create_draft_intake"),
                call("finalize_intake_summary", summary="Caller called about a matter."),
            ),
            tools(
                save("phone", "98765 43210"),
                call(
                    "finalize_intake_summary",
                    summary="Reach the caller on 91234 56789 before 2026-10-20.",
                ),
            ),
            say("Let me check a couple of details."),
        ],
        "Hello, here's my number: 98765 43210.",
    )
    results = tool_results(convo)
    assert results[1]["error"]["code"] == "VALIDATION_FAILED"  # nothing captured yet
    assert results[3]["error"]["details"]["unsupported"] == [
        "date 2026-10-20",
        "phone 91234 56789",
    ]
    assert the_intake(committed_demo).status is IntakeStatus.IN_PROGRESS


def blocked_events(demo: Demo) -> list[dict[str, Any]]:
    with demo[0]() as s:
        return list(
            s.scalars(
                select(AuditEvent.details).where(
                    AuditEvent.action == "agent.reply_blocked",
                    AuditEvent.outcome == AuditOutcome.REJECTED,
                )
            ).all()
        )


def test_false_booking_claim_is_blocked_and_regenerated(committed_demo: Demo) -> None:
    convo, llm = run(
        committed_demo,
        [
            tools(call("create_draft_intake")),
            say("Great news - your consultation is booked for Monday at 10."),
            say("I haven't booked anything yet. Would you like me to check available times?"),
        ],
        "Can I see a lawyer?",
    )
    assert convo.trace[-1].tool == "create_draft_intake"
    correction = llm.requests[-1][-1]
    assert correction["role"] == "user" and "no consultation is booked" in correction["content"]
    assert llm.tools_seen[-1] == []  # the rewrite may not call tools
    assert blocked_events(committed_demo) == [{"violations": [FALSE_BOOKING]}]
    # The blocked draft never enters the history.
    replies = [m["content"] for m in llm.requests[-1] if m["role"] == "assistant"]
    assert not any("is booked" in (r or "") for r in replies)


def test_persistent_false_claim_falls_back_to_a_safe_reply(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [
            say("A lawyer has already reviewed your case."),
            say("Don't worry, the lawyer has accepted your case."),
        ],
        "Has anyone looked at my case?",
    )
    last = convo._messages[-1]["content"]
    assert "nobody has accepted it yet" in last
    assert blocked_events(committed_demo) == [{"violations": [UNCONFIRMED_ACCEPTANCE]}]


def test_guard_rewrite_failure_still_returns_a_safe_reply(committed_demo: Demo) -> None:
    convo, _ = run(
        committed_demo,
        [say("Your appointment has been confirmed."), LLMUnavailable("timeout")],
        "Am I booked?",
    )
    assert "No consultation has been booked yet." in convo._messages[-1]["content"]
    assert not convo.ended  # a failed rewrite is not an outage of the conversation


def test_acceptance_claim_is_allowed_once_staff_accepted(committed_demo: Demo) -> None:
    sessions, firm = committed_demo
    convo, _ = run(
        committed_demo,
        [tools(call("create_draft_intake"), save("reported_deadline", TOMORROW)), say("Flagged.")],
        "Deadline tomorrow.",
    )
    with sessions() as s, s.begin():
        escalation = s.scalars(select(Escalation)).one()
        escalation_service.accept_escalation(
            s, firm_id=firm.firm_id, escalation_id=escalation.id, actor=firm.coordinator, now=NOW
        )
    convo.llm = ScriptedLLM([LLMResponse(content="Our coordinator has accepted your enquiry.")])
    assert convo.respond("Any update?") == "Our coordinator has accepted your enquiry."
    assert blocked_events(committed_demo) == []


def test_forgotten_deadline_triggers_one_nudge_and_the_escalation(committed_demo: Demo) -> None:
    convo, llm = run(
        committed_demo,
        [
            # The model forgets the deadline in its first draft...
            tools(call("create_draft_intake"), save("caller_name", "Priya Sharma")),
            say("Thanks, Priya. What phone number can we reach you on?"),
            # ...the system nudges it, and it saves the deadline before replying.
            tools(save("reported_deadline", TOMORROW)),
            say("Thanks. I've flagged the deadline for the team's prompt review."),
        ],
        "I'm Priya Sharma and I must respond to a notice by tomorrow.",
    )
    nudges = [
        m["content"]
        for m in llm.requests[-1]
        if m["content"] and str(m["content"]).startswith("[Automated check]")
    ]
    assert len(nudges) == 1 and "reported_deadline" in nudges[0]
    assert convo._messages[-1]["content"].startswith("Thanks. I've flagged")
    with committed_demo[0]() as s:
        assert s.scalars(select(Escalation.rule_id)).all() == ["imminent_deadline"]
        details = s.scalars(
            select(AuditEvent.details).where(AuditEvent.action == "agent.omission_nudged")
        ).all()
    assert details == [{"omissions": ["possible_deadline"]}]


def test_only_one_nudge_per_turn(committed_demo: Demo) -> None:
    convo, llm = run(
        committed_demo,
        [
            tools(call("create_draft_intake")),
            say("What's your name?"),
            say("What's your name?"),  # ignores the nudge: the reply still goes out
        ],
        "The hearing is next Friday.",
    )
    assert convo._messages[-1]["content"] == "What's your name?"
    assert len(llm.requests) == 3
