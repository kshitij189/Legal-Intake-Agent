"""Phase 1 exit criterion: manually create a draft intake, book a test appointment and create an
escalation, with correct database state verified from fresh sessions after real commits."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain import appointment_service, escalation_service, intake_service
from app.domain.actors import Actor
from app.models import AuditEvent, AvailabilitySlot
from app.models.enums import (
    AppointmentStatus,
    EscalationReason,
    EscalationStatus,
    FieldStatus,
    SlotStatus,
)
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm


def test_draft_intake_booking_and_escalation_end_to_end(
    committed_demo: tuple[sessionmaker[Session], SeededFirm],
) -> None:
    sessions, demo = committed_demo
    agent = Actor.agent()

    with sessions() as s, s.begin():
        intake = intake_service.create_draft_intake(s, firm_id=demo.firm_id, actor=agent, now=NOW)
        for key, status, value in [
            ("caller_name", FieldStatus.CONFIRMED_BY_CALLER, "Priya Sharma"),
            ("matter_category", FieldStatus.REPORTED_BY_CALLER, "EMPLOYMENT"),
            ("matter_summary", FieldStatus.REPORTED_BY_CALLER, "Employer withheld salary."),
            ("reported_deadline", FieldStatus.REPORTED_BY_CALLER, "2026-10-13"),
        ]:
            intake_service.save_intake_answer(
                s,
                firm_id=demo.firm_id,
                intake_id=intake.id,
                actor=agent,
                field_key=key,
                status=status,
                value=value,
                now=NOW,
            )
        intake_id = intake.id

    with sessions() as s, s.begin():
        escalation_id = escalation_service.create_escalation(
            s,
            firm_id=demo.firm_id,
            actor=agent,
            intake_id=intake_id,
            reason_code=EscalationReason.CONFIGURED_RULE,
            rule_id="imminent_deadline",
            idempotency_key="exit-escalation-1",
            now=NOW,
        ).escalation.id

    with sessions() as s, s.begin():
        slot_id = appointment_service.check_availability(
            s, firm_id=demo.firm_id, matter_category="EMPLOYMENT", now=NOW
        )[0].id
        appointment_id = appointment_service.book_consultation(
            s,
            firm_id=demo.firm_id,
            actor=agent,
            intake_id=intake_id,
            slot_id=slot_id,
            idempotency_key="exit-booking-1",
            now=NOW,
        ).appointment.id

    with sessions() as s:
        intake = intake_service.get_intake(s, firm_id=demo.firm_id, intake_id=intake_id)
        fields = intake_service.intake_fields(intake)
        assert fields["reported_deadline"].status is FieldStatus.REPORTED_BY_CALLER  # not verified
        assert fields["phone"].status is FieldStatus.MISSING

        appointment = appointment_service.get_appointment(
            s, firm_id=demo.firm_id, appointment_id=appointment_id
        )
        assert appointment.status is AppointmentStatus.BOOKED
        assert appointment.intake_id == intake_id
        assert s.get_one(AvailabilitySlot, slot_id).status is SlotStatus.BOOKED

        escalation = escalation_service.get_escalation(
            s, firm_id=demo.firm_id, escalation_id=escalation_id
        )
        assert escalation.status is EscalationStatus.PENDING
        assert escalation.intake_id == intake_id

        actions = s.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.firm_id == demo.firm_id)
            .order_by(AuditEvent.seq)
        ).all()
        assert actions == [
            "intake.created",
            *["intake.field_saved"] * 4,
            "escalation.created",
            "appointment.booked",
        ]
