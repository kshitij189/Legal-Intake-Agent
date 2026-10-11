"""Tool executor against PostgreSQL: the model cannot bypass validation or business rules."""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.tool_registry import REGISTRY
from app.domain import appointment_service, call_session_service, intake_service
from app.domain.actors import Actor
from app.domain.appointment_service import check_availability
from app.models import Appointment, AuditEvent, Escalation, IntakeRecord
from app.models.enums import AuditOutcome, FieldStatus, IntakeStatus, SessionChannel
from app.tools.context import ToolContext
from app.tools.executor import ToolExecutor, ToolSpec
from app.tools.schemas import NoArguments, ToolResult
from tests.helpers import FIRM_TODAY, NOW
from tests.integration.conftest import SeededFirm

TOMORROW = (FIRM_TODAY + timedelta(days=1)).isoformat()


@dataclass
class Harness:
    sessions: sessionmaker[Session]
    firm: SeededFirm
    ctx: ToolContext
    executor: ToolExecutor

    def run(self, name: str, **args: Any) -> ToolResult:
        return self.executor.execute(name, json.dumps(args))

    def ok(self, name: str, **args: Any) -> dict[str, Any]:
        result = self.run(name, **args)
        assert result.ok, result.to_json()
        assert result.data is not None
        return result.data

    def error(self, name: str, **args: Any) -> str:
        result = self.run(name, **args)
        assert not result.ok, result.to_json()
        assert result.error is not None
        return result.error.code

    def intake(self) -> IntakeRecord:
        with self.sessions() as s:
            records = s.scalars(select(IntakeRecord)).all()
            assert len(records) == 1
            return records[0]


def make_harness(
    sessions: sessionmaker[Session], firm: SeededFirm, registry: dict[str, ToolSpec] | None = None
) -> Harness:
    agent = Actor.agent()
    with sessions() as s, s.begin():
        call = call_session_service.start_call_session(
            s, firm_id=firm.firm_id, actor=agent, channel=SessionChannel.TEXT
        )
    ctx = ToolContext(firm_id=firm.firm_id, call_session_id=call.id, actor=agent, clock=lambda: NOW)
    return Harness(sessions, firm, ctx, ToolExecutor(sessions, ctx, registry or REGISTRY))


@pytest.fixture
def h(committed_demo: tuple[sessionmaker[Session], SeededFirm]) -> Iterator[Harness]:
    yield make_harness(*committed_demo)


def test_answers_require_a_draft_intake_first(h: Harness) -> None:
    assert (
        h.error(
            "save_intake_answer",
            field_key="caller_name",
            status="REPORTED_BY_CALLER",
            value="Priya",
        )
        == "INTAKE_NOT_STARTED"
    )
    created = h.ok("create_draft_intake")
    again = h.ok("create_draft_intake")
    assert created["created"] and not again["created"]
    assert created["intake_reference"] == again["intake_reference"]
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(IntakeRecord)) == 1


def test_saved_answer_is_persisted_with_provenance(h: Harness) -> None:
    h.ok("create_draft_intake")
    data = h.ok(
        "save_intake_answer", field_key="phone", status="REPORTED_BY_CALLER", value="98765 43210"
    )
    assert data["saved"] == {"value": "9876543210", "status": "REPORTED_BY_CALLER"}
    assert "phone" not in data["missing_required_fields"]
    stored = intake_service.intake_fields(h.intake())["phone"]
    assert (stored.value, stored.status) == ("9876543210", FieldStatus.REPORTED_BY_CALLER)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (
            {"field_key": "caller_name", "status": "VERIFIED_BY_SYSTEM", "value": "X"},
            "INVALID_ARGUMENTS",
        ),
        ({"field_key": "caller_name", "status": "MISSING"}, "INVALID_ARGUMENTS"),
        (
            {
                "field_key": "caller_name",
                "status": "REPORTED_BY_CALLER",
                "value": "X",
                "intake_id": str(uuid.uuid4()),
            },
            "INVALID_ARGUMENTS",
        ),
        (
            {
                "field_key": "caller_name",
                "status": "REPORTED_BY_CALLER",
                "value": "X",
                "firm_id": str(uuid.uuid4()),
            },
            "INVALID_ARGUMENTS",
        ),
        ({"field_key": "name", "status": "REPORTED_BY_CALLER", "value": "X"}, "VALIDATION_FAILED"),
        (
            {"field_key": "reported_deadline", "status": "REPORTED_BY_CALLER", "value": "tomorrow"},
            "VALIDATION_FAILED",
        ),
        (
            {"field_key": "matter_category", "status": "REPORTED_BY_CALLER", "value": "CRIMINAL"},
            "VALIDATION_FAILED",
        ),
        (
            {"field_key": "phone", "status": "DECLINED_BY_CALLER", "value": "123"},
            "VALIDATION_FAILED",
        ),
    ],
)
def test_invalid_answers_are_rejected_and_audited(
    h: Harness, args: dict[str, Any], code: str
) -> None:
    h.ok("create_draft_intake")
    before = h.intake().fields
    assert h.error("save_intake_answer", **args) == code
    assert h.intake().fields == before
    with h.sessions() as s:
        audit = s.scalars(
            select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.REJECTED)
        ).one()
    assert (audit.action, audit.details) == ("tool.save_intake_answer", {"error_code": code})
    assert audit.entity_id == h.ctx.call_session_id


def test_malformed_calls_are_rejected(h: Harness) -> None:
    def code(name: str, raw: str) -> str | None:
        result = h.executor.execute(name, raw)
        return result.error.code if result.error else None

    assert code("run_sql", '{"query": "DROP TABLE firms"}') == "UNKNOWN_TOOL"
    assert code("create_draft_intake", "{not json") == "INVALID_ARGUMENTS"
    assert code("create_draft_intake", "[1, 2]") == "INVALID_ARGUMENTS"


def test_imminent_deadline_escalates_deterministically_once(h: Harness) -> None:
    h.ok("create_draft_intake")
    first = h.ok(
        "save_intake_answer",
        field_key="reported_deadline",
        status="REPORTED_BY_CALLER",
        value=TOMORROW,
    )
    again = h.ok(
        "save_intake_answer",
        field_key="reported_deadline",
        status="CONFIRMED_BY_CALLER",
        value=TOMORROW,
    )
    [escalation] = first["escalations_for_human_review"]
    assert escalation["rule_id"] == "imminent_deadline"
    assert (escalation["status"], escalation["priority"], escalation["newly_created"]) == (
        "PENDING",
        "URGENT",
        True,
    )
    assert again["escalations_for_human_review"][0]["newly_created"] is False
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(Escalation)) == 1

    handoff = h.ok("get_handoff_status")["escalations"]
    assert [(e["status"], e["accepted_by_staff"]) for e in handoff] == [("PENDING", False)]


def test_distant_deadline_does_not_escalate_and_rule_cannot_be_forced(h: Harness) -> None:
    h.ok("create_draft_intake")
    data = h.ok(
        "save_intake_answer",
        field_key="reported_deadline",
        status="REPORTED_BY_CALLER",
        value="2026-12-31",
    )
    assert data["escalations_for_human_review"] == []
    assert (
        h.error("mark_for_human_review", reason="CONFIGURED_RULE", rule_id="imminent_deadline")
        == "ESCALATION_RULE_NOT_MATCHED"
    )
    assert (
        h.error("mark_for_human_review", reason="CONFIGURED_RULE", rule_id="invented_rule")
        == "VALIDATION_FAILED"
    )


def test_unable_to_complete_review_is_verified_and_not_called_accepted(h: Harness) -> None:
    h.ok("create_draft_intake")
    data = h.ok(
        "mark_for_human_review",
        reason="UNABLE_TO_COMPLETE",
        description="Caller's audio kept dropping.",
    )
    assert (data["status"], data["priority"]) == ("PENDING", "HIGH")
    assert "nobody has accepted it" in data["meaning"]
    repeat = h.ok("mark_for_human_review", reason="UNABLE_TO_COMPLETE", description="again")
    assert repeat["escalation_id"] == data["escalation_id"] and not repeat["newly_created"]


def test_booking_only_offered_slots_and_never_twice(h: Harness) -> None:
    h.ok("create_draft_intake")
    h.ok(
        "save_intake_answer",
        field_key="matter_category",
        status="REPORTED_BY_CALLER",
        value="EMPLOYMENT",
    )
    with h.sessions() as s:
        unoffered = check_availability(s, firm_id=h.firm.firm_id, now=NOW, limit=50)[-1].id
    assert h.error("book_consultation", slot_id=str(unoffered)) == "SLOT_NOT_OFFERED"

    offer = h.ok("check_appointment_availability")
    assert offer["matter_category"] == "EMPLOYMENT"  # taken from the saved intake
    assert len(offer["slots"]) == 3
    assert {s["lawyer"] for s in offer["slots"]} <= {"Aarav Mehta", "Daniel Brooks"}
    slot_id = offer["slots"][0]["slot_id"]

    booked = h.ok("book_consultation", slot_id=slot_id)
    assert booked["verified"] is True and booked["status"] == "BOOKED"
    assert booked["replayed"] is False and "IST" in booked["starts_at"]
    retry = h.ok("book_consultation", slot_id=slot_id)
    assert retry["replayed"] is True and retry["appointment_id"] == booked["appointment_id"]
    assert h.error("book_consultation", slot_id=offer["slots"][1]["slot_id"]) == "ALREADY_BOOKED"
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(Appointment)) == 1

    status = h.ok("get_intake_status")
    assert status["booked_appointment"]["appointment_id"] == booked["appointment_id"]


def test_finalize_submits_for_review_and_locks_answers(h: Harness) -> None:
    h.ok("create_draft_intake")
    h.ok("save_intake_answer", field_key="phone", status="DECLINED_BY_CALLER")
    data = h.ok("finalize_intake_summary", summary="Caller reports withheld salary.")
    assert data["status"] == "PENDING_REVIEW"
    assert data["declined_fields"] == ["phone"]
    assert "caller_name" in data["missing_required_fields"]
    assert h.intake().status is IntakeStatus.PENDING_REVIEW
    assert h.intake().summary == "Caller reports withheld salary."
    assert (
        h.error(
            "save_intake_answer", field_key="caller_name", status="REPORTED_BY_CALLER", value="Late"
        )
        == "INVALID_TRANSITION"
    )
    assert h.error("finalize_intake_summary", summary="again") == "INVALID_TRANSITION"


def test_get_intake_questions_returns_firm_config(h: Harness) -> None:
    questions = h.ok("get_intake_questions")["questions"]
    category = next(q for q in questions if q["key"] == "matter_category")
    assert [c["code"] for c in category["choices"]][:2] == ["EMPLOYMENT", "FAMILY"]


def test_conversation_cannot_reach_another_firms_intake(
    committed_demo: tuple[sessionmaker[Session], SeededFirm],
) -> None:
    sessions, demo = committed_demo
    first = make_harness(sessions, demo)
    first.ok("create_draft_intake")
    second = make_harness(sessions, demo)
    # A fresh conversation has no intake of its own and no way to name someone else's.
    assert second.error("get_intake_status") == "INTAKE_NOT_STARTED"
    assert (
        second.error(
            "save_intake_answer",
            field_key="caller_name",
            status="REPORTED_BY_CALLER",
            value="X",
            intake_reference="INT-1001",
        )
        == "INVALID_ARGUMENTS"
    )


def test_unexpected_errors_become_internal_error_without_success(
    committed_demo: tuple[sessionmaker[Session], SeededFirm],
) -> None:
    def boom(session: Session, ctx: ToolContext, _: NoArguments) -> dict[str, Any]:
        raise RuntimeError("database exploded")

    registry = {"broken": ToolSpec("broken", "test", NoArguments, boom)}
    harness = make_harness(*committed_demo, registry=registry)
    result = harness.run("broken")
    assert not result.ok and result.error is not None
    assert result.error.code == "INTERNAL_ERROR"
    assert "nothing should be confirmed" in result.error.message
    with harness.sessions() as s:
        outcome = s.scalar(select(AuditEvent.outcome).where(AuditEvent.action == "tool.broken"))
    assert outcome is AuditOutcome.FAILURE


# --- durable booking path (Temporal workflow stubbed; the real workflow is in test_workflows) ---


def offered_slot(h: Harness) -> str:
    h.ok("create_draft_intake")
    h.ok(
        "save_intake_answer",
        field_key="matter_category",
        status="REPORTED_BY_CALLER",
        value="EMPLOYMENT",
    )
    slot: str = h.ok("check_appointment_availability")["slots"][0]["slot_id"]
    return slot


def test_durable_booking_success_is_verified_from_the_database(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tools import calendar_tools
    from app.workflows.types import BookingOutcome

    def fake_workflow(settings: Any, inp: Any, wait: float) -> BookingOutcome:
        with h.sessions() as s, s.begin():  # the workflow's activity books for real
            result = appointment_service.book_consultation(
                s,
                firm_id=uuid.UUID(inp.firm_id),
                actor=Actor.agent(),
                intake_id=uuid.UUID(inp.intake_id),
                slot_id=uuid.UUID(inp.slot_id),
                idempotency_key=inp.idempotency_key,
                now=NOW,
            )
        return BookingOutcome(status="BOOKED", appointment_id=str(result.appointment.id))

    monkeypatch.setattr(calendar_tools, "run_booking_workflow", fake_workflow)
    h.ctx.durable_booking = True
    booked = h.ok("book_consultation", slot_id=offered_slot(h))
    assert booked["verified"] is True and booked["status"] == "BOOKED"


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (
            {"status": "REJECTED", "error_code": "SLOT_UNAVAILABLE", "message": "taken"},
            "SLOT_UNAVAILABLE",
        ),
        (
            {"status": "FAILED", "error_code": "CALENDAR_UNAVAILABLE", "message": "timeout"},
            "BOOKING_NOT_CONFIRMED",
        ),
        (None, "BOOKING_NOT_CONFIRMED"),  # still running after the wait: never claim it
    ],
)
def test_durable_booking_failures_are_never_confirmed(
    h: Harness, monkeypatch: pytest.MonkeyPatch, outcome: dict[str, str] | None, code: str
) -> None:
    from app.tools import calendar_tools
    from app.workflows.types import BookingOutcome

    monkeypatch.setattr(
        calendar_tools,
        "run_booking_workflow",
        lambda *_: BookingOutcome(**outcome) if outcome else None,
    )
    h.ctx.durable_booking = True
    assert h.error("book_consultation", slot_id=offered_slot(h)) == code
    with h.sessions() as s:
        assert s.scalar(select(func.count()).select_from(Appointment)) == 0


def test_unreachable_temporal_falls_back_to_direct_booking(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tools import calendar_tools
    from app.workflows.client import TemporalUnavailable

    def unreachable(*_: Any) -> None:
        raise TemporalUnavailable("connection refused")

    monkeypatch.setattr(calendar_tools, "run_booking_workflow", unreachable)
    h.ctx.durable_booking = True
    assert h.ok("book_consultation", slot_id=offered_slot(h))["verified"] is True
