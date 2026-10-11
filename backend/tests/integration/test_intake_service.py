import re

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import intake_service
from app.domain.actors import Actor
from app.domain.errors import InvalidTransition, NotFound, PermissionDenied, ValidationFailed
from app.models import AuditEvent, Firm, IntakeRecord
from app.models.enums import ActorType, FieldStatus, IntakeStatus
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm

AGENT = Actor.agent()


def new_intake(session: Session, firm: SeededFirm) -> IntakeRecord:
    return intake_service.create_draft_intake(session, firm_id=firm.firm_id, actor=AGENT, now=NOW)


def test_create_draft_intake(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo)
    assert re.fullmatch(r"INT-\d{4,}", intake.reference)
    assert intake.status is IntakeStatus.IN_PROGRESS
    fields = intake_service.intake_fields(intake)
    assert set(fields) >= {"caller_name", "phone", "matter_category", "reported_deadline"}
    assert all(e.status is FieldStatus.MISSING and e.value is None for e in fields.values())

    event = session.scalars(select(AuditEvent).where(AuditEvent.entity_id == intake.id)).one()
    assert (event.action, event.actor_type) == ("intake.created", ActorType.AGENT)


def test_save_answer_persists_normalized_value_with_provenance(
    session: Session, demo: SeededFirm
) -> None:
    intake = new_intake(session, demo)
    intake_service.save_intake_answer(
        session,
        firm_id=demo.firm_id,
        intake_id=intake.id,
        actor=AGENT,
        field_key="phone",
        status=FieldStatus.CONFIRMED_BY_CALLER,
        value="+91 98765 43210",
        now=NOW,
    )
    session.expire_all()
    stored = intake_service.intake_fields(
        intake_service.get_intake(session, firm_id=demo.firm_id, intake_id=intake.id)
    )["phone"]
    assert (stored.value, stored.status, stored.source) == (
        "+919876543210",
        FieldStatus.CONFIRMED_BY_CALLER,
        ActorType.AGENT,
    )
    event = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "intake.field_saved")
    ).one()
    assert event.details == {
        "field_key": "phone",
        "previous_status": "MISSING",
        "status": "CONFIRMED_BY_CALLER",
    }
    assert "98765" not in str(event.details)  # contact details stay out of the audit log


def test_refusal_is_recorded_not_invented(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo)
    intake_service.save_intake_answer(
        session,
        firm_id=demo.firm_id,
        intake_id=intake.id,
        actor=AGENT,
        field_key="phone",
        status=FieldStatus.DECLINED_BY_CALLER,
        now=NOW,
    )
    entry = intake_service.intake_fields(intake)["phone"]
    assert entry.status is FieldStatus.DECLINED_BY_CALLER
    assert entry.value is None


@pytest.mark.parametrize(
    ("field_key", "value"),
    [("not_a_field", "x"), ("reported_deadline", "tomorrow"), ("matter_category", "CRIMINAL")],
)
def test_invalid_answers_are_rejected_without_changing_state(
    session: Session, demo: SeededFirm, field_key: str, value: str
) -> None:
    intake = new_intake(session, demo)
    before = dict(intake.fields)
    with pytest.raises(ValidationFailed):
        intake_service.save_intake_answer(
            session,
            firm_id=demo.firm_id,
            intake_id=intake.id,
            actor=AGENT,
            field_key=field_key,
            status=FieldStatus.REPORTED_BY_CALLER,
            value=value,
            now=NOW,
        )
    assert intake.fields == before


def test_only_reviewers_can_mark_fields_verified(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo)
    kwargs = dict(
        firm_id=demo.firm_id,
        intake_id=intake.id,
        field_key="reported_deadline",
        status=FieldStatus.VERIFIED_BY_SYSTEM,
        value="2026-10-13",
        now=NOW,
    )
    with pytest.raises(PermissionDenied):
        intake_service.save_intake_answer(session, actor=AGENT, **kwargs)
    with pytest.raises(PermissionDenied):  # administrators configure, they don't review
        intake_service.save_intake_answer(session, actor=demo.actor("rohan"), **kwargs)
    intake_service.save_intake_answer(session, actor=demo.coordinator, **kwargs)
    assert intake_service.intake_fields(intake)["reported_deadline"].status is (
        FieldStatus.VERIFIED_BY_SYSTEM
    )


def test_lifecycle_transitions_and_edit_locks(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo)
    ids = dict(firm_id=demo.firm_id, intake_id=intake.id)

    intake_service.transition_intake(session, actor=AGENT, target=IntakeStatus.INCOMPLETE, **ids)
    intake_service.transition_intake(
        session, actor=AGENT, target=IntakeStatus.PENDING_REVIEW, **ids
    )
    with pytest.raises(InvalidTransition):  # agent may not edit an intake under review
        intake_service.save_intake_answer(
            session,
            actor=AGENT,
            field_key="caller_name",
            status=FieldStatus.REPORTED_BY_CALLER,
            value="Priya",
            now=NOW,
            **ids,
        )
    intake_service.save_intake_answer(
        session,
        actor=demo.coordinator,
        field_key="caller_name",
        status=FieldStatus.REPORTED_BY_CALLER,
        value="Priya",
        now=NOW,
        **ids,
    )
    with pytest.raises(PermissionDenied):  # closing is a staff decision
        intake_service.transition_intake(session, actor=AGENT, target=IntakeStatus.CLOSED, **ids)
    intake_service.transition_intake(
        session, actor=demo.coordinator, target=IntakeStatus.CLOSED, **ids
    )
    with pytest.raises(InvalidTransition):
        intake_service.transition_intake(
            session, actor=demo.coordinator, target=IntakeStatus.IN_PROGRESS, **ids
        )
    actions = session.scalars(
        select(AuditEvent.details)
        .where(AuditEvent.entity_id == intake.id, AuditEvent.action == "intake.status_changed")
        .order_by(AuditEvent.seq)
    ).all()
    assert [d["to"] for d in actions] == ["INCOMPLETE", "PENDING_REVIEW", "CLOSED"]


def test_tenant_isolation(session: Session, demo: SeededFirm, other_firm: SeededFirm) -> None:
    intake = new_intake(session, demo)
    with pytest.raises(NotFound):
        intake_service.get_intake(session, firm_id=other_firm.firm_id, intake_id=intake.id)
    with pytest.raises(NotFound):
        intake_service.save_intake_answer(
            session,
            firm_id=other_firm.firm_id,
            intake_id=intake.id,
            actor=AGENT,
            field_key="caller_name",
            status=FieldStatus.REPORTED_BY_CALLER,
            value="X",
            now=NOW,
        )
    # Staff of another firm cannot act here even with a valid staff id.
    with pytest.raises(PermissionDenied):
        intake_service.save_intake_answer(
            session,
            firm_id=demo.firm_id,
            intake_id=intake.id,
            actor=other_firm.coordinator,
            field_key="caller_name",
            status=FieldStatus.REPORTED_BY_CALLER,
            value="X",
            now=NOW,
        )


def test_inactive_firm_cannot_take_intakes(session: Session, demo: SeededFirm) -> None:
    session.get_one(Firm, demo.firm_id).is_active = False
    session.flush()
    with pytest.raises(NotFound):
        new_intake(session, demo)
