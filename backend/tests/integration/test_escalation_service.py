import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import escalation_service, intake_service
from app.domain.actors import Actor
from app.domain.errors import (
    IdempotencyConflict,
    InvalidTransition,
    NotFound,
    PermissionDenied,
    RuleNotMatched,
    ValidationFailed,
)
from app.models import AuditEvent, IntakeRecord, StaffMember
from app.models.enums import ActorType, EscalationReason, EscalationStatus, FieldStatus, Priority
from tests.helpers import FIRM_TODAY, NOW
from tests.integration.conftest import SeededFirm

AGENT = Actor.agent()
RULE = EscalationReason.CONFIGURED_RULE


def intake_with_deadline(session: Session, firm: SeededFirm, days_ahead: int) -> IntakeRecord:
    intake = intake_service.create_draft_intake(session, firm_id=firm.firm_id, actor=AGENT, now=NOW)
    intake_service.save_intake_answer(
        session,
        firm_id=firm.firm_id,
        intake_id=intake.id,
        actor=AGENT,
        field_key="reported_deadline",
        status=FieldStatus.REPORTED_BY_CALLER,
        value=(FIRM_TODAY + timedelta(days=days_ahead)).isoformat(),
        now=NOW,
    )
    return intake


def escalate(
    session: Session,
    firm: SeededFirm,
    intake_id: uuid.UUID,
    key: str,
    rule_id: str | None = "imminent_deadline",
    reason: EscalationReason = RULE,
    description: str | None = None,
) -> escalation_service.EscalationResult:
    return escalation_service.create_escalation(
        session,
        firm_id=firm.firm_id,
        actor=AGENT,
        intake_id=intake_id,
        reason_code=reason,
        rule_id=rule_id,
        description=description,
        idempotency_key=key,
        now=NOW,
    )


def test_reported_deadline_tomorrow_creates_urgent_pending_escalation(
    session: Session, demo: SeededFirm
) -> None:
    intake = intake_with_deadline(session, demo, 1)
    matches = escalation_service.evaluate_escalation_rules(
        session, firm_id=demo.firm_id, intake_id=intake.id, now=NOW
    )
    assert [m.rule_id for m in matches] == ["imminent_deadline"]

    result = escalate(session, demo, intake.id, "esc-key-0001")
    session.expire_all()
    stored = escalation_service.get_escalation(
        session, firm_id=demo.firm_id, escalation_id=result.escalation.id
    )
    assert result.created
    assert (stored.status, stored.priority, stored.rule_id) == (
        EscalationStatus.PENDING,
        Priority.URGENT,
        "imminent_deadline",
    )
    assert stored.reason_text.startswith("Caller reported a deadline within 3 days")
    assert stored.assigned_staff_id is None  # nobody has accepted it
    assert escalation_service.list_escalations(
        session, firm_id=demo.firm_id, statuses=[EscalationStatus.PENDING]
    ) == [stored]
    event = session.scalars(select(AuditEvent).where(AuditEvent.entity_id == stored.id)).one()
    assert event.action == "escalation.created"


def test_rule_must_match_persisted_state(session: Session, demo: SeededFirm) -> None:
    intake = intake_with_deadline(session, demo, 10)
    with pytest.raises(RuleNotMatched):
        escalate(session, demo, intake.id, "esc-key-0002")
    with pytest.raises(ValidationFailed, match="unknown escalation rule"):
        escalate(session, demo, intake.id, "esc-key-0003", rule_id="model_invented_rule")
    with pytest.raises(ValidationFailed, match="rule_id"):
        escalate(session, demo, intake.id, "esc-key-0004", rule_id=None)
    assert escalation_service.list_escalations(session, firm_id=demo.firm_id) == []


def test_repeat_escalation_for_same_rule_returns_existing(
    session: Session, demo: SeededFirm
) -> None:
    intake = intake_with_deadline(session, demo, 0)
    first = escalate(session, demo, intake.id, "esc-key-0005")
    again = escalate(session, demo, intake.id, "esc-key-0006")
    replay = escalate(session, demo, intake.id, "esc-key-0005")
    assert (again.created, replay.created) == (False, False)
    assert first.escalation.id == again.escalation.id == replay.escalation.id
    with pytest.raises(IdempotencyConflict):
        escalate(
            session,
            demo,
            intake.id,
            "esc-key-0005",
            rule_id=None,
            reason=EscalationReason.UNABLE_TO_COMPLETE,
            description="x",
        )


def test_unable_to_complete_escalation(session: Session, demo: SeededFirm) -> None:
    intake = intake_with_deadline(session, demo, 30)
    with pytest.raises(ValidationFailed):
        escalate(
            session,
            demo,
            intake.id,
            "esc-key-0007",
            rule_id=None,
            reason=EscalationReason.UNABLE_TO_COMPLETE,
        )
    with pytest.raises(ValidationFailed, match="only valid"):
        escalate(
            session,
            demo,
            intake.id,
            "esc-key-0008",
            reason=EscalationReason.UNABLE_TO_COMPLETE,
            description="audio unclear",
        )
    result = escalate(
        session,
        demo,
        intake.id,
        "esc-key-0009",
        rule_id=None,
        reason=EscalationReason.UNABLE_TO_COMPLETE,
        description="Caller's answers could not be understood after two attempts.",
    )
    assert result.created
    assert result.escalation.priority is Priority.HIGH


def test_only_reviewing_staff_of_the_firm_can_accept(
    session: Session, demo: SeededFirm, other_firm: SeededFirm
) -> None:
    escalation_id = escalate(
        session, demo, intake_with_deadline(session, demo, 1).id, "esc-key-0010"
    ).escalation.id

    def accept(actor: Actor) -> None:
        escalation_service.accept_escalation(
            session, firm_id=demo.firm_id, escalation_id=escalation_id, actor=actor, now=NOW
        )

    for actor in (
        AGENT,
        Actor.system(),
        demo.actor("rohan"),
        other_firm.coordinator,
        Actor(ActorType.STAFF, "not-a-staff-uuid"),
        Actor.staff(uuid.uuid4()),
    ):
        with pytest.raises(PermissionDenied):
            accept(actor)

    session.get_one(StaffMember, demo.staff("meera")).is_active = False
    session.flush()
    with pytest.raises(PermissionDenied):
        accept(demo.actor("meera"))

    accept(demo.coordinator)
    stored = escalation_service.get_escalation(
        session, firm_id=demo.firm_id, escalation_id=escalation_id
    )
    assert stored.status is EscalationStatus.ACCEPTED
    assert stored.assigned_staff_id == demo.staff("sana.kapoor")
    assert stored.accepted_at == NOW
    with pytest.raises(InvalidTransition):
        accept(demo.actor("aarav"))


def test_resolve_requires_note_and_is_terminal(session: Session, demo: SeededFirm) -> None:
    intake = intake_with_deadline(session, demo, 1)
    escalation_id = escalate(session, demo, intake.id, "esc-key-0011").escalation.id
    ids = dict(firm_id=demo.firm_id, escalation_id=escalation_id)
    with pytest.raises(ValidationFailed):
        escalation_service.resolve_escalation(
            session, actor=demo.actor("aarav"), resolution_note="  ", now=NOW, **ids
        )
    escalation_service.resolve_escalation(
        session,
        actor=demo.actor("aarav"),
        resolution_note="Called back; consult booked.",
        now=NOW,
        **ids,
    )
    with pytest.raises(InvalidTransition):
        escalation_service.accept_escalation(session, actor=demo.coordinator, now=NOW, **ids)

    # Once resolved, the same rule can raise a fresh escalation for the intake.
    assert escalate(session, demo, intake.id, "esc-key-0012").created


def test_failed_handoff_is_recorded_by_system_only(session: Session, demo: SeededFirm) -> None:
    escalation_id = escalate(
        session, demo, intake_with_deadline(session, demo, 1).id, "esc-key-0013"
    ).escalation.id
    ids = dict(firm_id=demo.firm_id, escalation_id=escalation_id)
    with pytest.raises(PermissionDenied):
        escalation_service.mark_escalation_failed(
            session, actor=AGENT, error="queue down", now=NOW, **ids
        )
    escalation_service.mark_escalation_failed(
        session,
        actor=Actor.system("handoff-workflow"),
        error="review queue unavailable",
        now=NOW,
        **ids,
    )
    stored = escalation_service.get_escalation(session, **ids)
    assert (stored.status, stored.last_error) == (
        EscalationStatus.FAILED,
        "review queue unavailable",
    )
    with pytest.raises(InvalidTransition):  # a failed handoff is never reported as accepted
        escalation_service.accept_escalation(session, actor=demo.coordinator, now=NOW, **ids)


def test_escalations_are_tenant_scoped(
    session: Session, demo: SeededFirm, other_firm: SeededFirm
) -> None:
    intake = intake_with_deadline(session, demo, 1)
    escalation_id = escalate(session, demo, intake.id, "esc-key-0014").escalation.id
    with pytest.raises(NotFound):
        escalation_service.get_escalation(
            session, firm_id=other_firm.firm_id, escalation_id=escalation_id
        )
    with pytest.raises(NotFound):
        escalate(session, other_firm, intake.id, "esc-key-0015")
    assert escalation_service.list_escalations(session, firm_id=other_firm.firm_id) == []
