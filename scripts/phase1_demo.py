"""Phase 1 exit-criterion walkthrough against the real database (no LLM involved).

Creates a draft intake, records caller answers with provenance, escalates via a configured rule,
books a consultation (and replays the request to show idempotency), lets the coordinator accept
the escalation, then re-reads everything in a fresh session and prints it.

Run after `alembic upgrade head` and `scripts/seed_demo_data.py`. Writes fictional records.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.domain import appointment_service, escalation_service, intake_service
from app.domain.actors import Actor
from app.models import AuditEvent, Firm, StaffMember
from app.models.enums import EscalationReason, FieldStatus, StaffRole


def main() -> None:
    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    agent = Actor.agent()
    now = datetime.now(UTC)
    run = uuid.uuid4().hex[:8]

    with sessions() as session, session.begin():
        firm = session.scalars(select(Firm).where(Firm.slug == settings.demo_firm_slug)).one()
        tomorrow = (now.astimezone(ZoneInfo(firm.timezone)) + timedelta(days=1)).date()

        intake = intake_service.create_draft_intake(session, firm_id=firm.id, actor=agent)
        answers = [
            (
                "caller_name",
                FieldStatus.CONFIRMED_BY_CALLER,
                "Priya Sharma (fictional)",
            ),
            ("matter_category", FieldStatus.REPORTED_BY_CALLER, "EMPLOYMENT"),
            (
                "matter_summary",
                FieldStatus.REPORTED_BY_CALLER,
                "Caller reports withheld salary.",
            ),
            ("reported_deadline", FieldStatus.REPORTED_BY_CALLER, tomorrow.isoformat()),
            ("preferred_contact_method", FieldStatus.CONFIRMED_BY_CALLER, "PHONE"),
        ]
        for key, status, value in answers:
            intake_service.save_intake_answer(
                session,
                firm_id=firm.id,
                intake_id=intake.id,
                actor=agent,
                field_key=key,
                status=status,
                value=value,
            )
        intake_service.save_intake_answer(
            session,
            firm_id=firm.id,
            intake_id=intake.id,
            actor=agent,
            field_key="phone",
            status=FieldStatus.DECLINED_BY_CALLER,
        )
        firm_id, intake_id = firm.id, intake.id

    with sessions() as session, session.begin():
        matches = escalation_service.evaluate_escalation_rules(
            session, firm_id=firm_id, intake_id=intake_id
        )
        print("rule matches:", [m.rule_id for m in matches])
        escalation = escalation_service.create_escalation(
            session,
            firm_id=firm_id,
            actor=agent,
            intake_id=intake_id,
            reason_code=EscalationReason.CONFIGURED_RULE,
            rule_id=matches[0].rule_id,
            idempotency_key=f"demo-esc-{run}",
        ).escalation
        escalation_id = escalation.id

    with sessions() as session, session.begin():
        slots = appointment_service.check_availability(
            session, firm_id=firm_id, matter_category="EMPLOYMENT", limit=3
        )
        print("offered slots:", [s.starts_at.isoformat() for s in slots])
        slot_id = slots[0].id

    key = f"demo-book-{run}"
    results = []
    for _ in range(2):  # second call simulates a retry after a timeout
        with sessions() as session, session.begin():
            results.append(
                appointment_service.book_consultation(
                    session,
                    firm_id=firm_id,
                    actor=agent,
                    intake_id=intake_id,
                    slot_id=slot_id,
                    idempotency_key=key,
                )
            )
    print(
        "booking replayed on retry:",
        results[1].replayed,
        "| same appointment:",
        results[0].appointment.id == results[1].appointment.id,
    )

    with sessions() as session, session.begin():
        coordinator = session.scalars(
            select(StaffMember).where(
                StaffMember.firm_id == firm_id,
                StaffMember.role == StaffRole.INTAKE_COORDINATOR,
            )
        ).first()
        assert coordinator is not None
        escalation_service.accept_escalation(
            session,
            firm_id=firm_id,
            escalation_id=escalation_id,
            actor=Actor.staff(coordinator.id),
        )

    # Verify from a fresh session: only persisted state counts.
    with sessions() as session:
        intake = intake_service.get_intake(session, firm_id=firm_id, intake_id=intake_id)
        appointment = appointment_service.get_appointment(
            session, firm_id=firm_id, appointment_id=results[0].appointment.id
        )
        escalation = escalation_service.get_escalation(
            session, firm_id=firm_id, escalation_id=escalation_id
        )
        audit = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.firm_id == firm_id)
            .where(AuditEvent.entity_id.in_([intake_id, appointment.id, escalation_id]))
            .order_by(AuditEvent.seq)
        ).all()
        print(
            json.dumps(
                {
                    "intake": {
                        "reference": intake.reference,
                        "status": intake.status,
                        "fields": {
                            k: {"value": v["value"], "status": v["status"]}
                            for k, v in intake.fields.items()
                        },
                    },
                    "appointment": {
                        "id": str(appointment.id),
                        "status": appointment.status,
                        "starts_at": appointment.starts_at.isoformat(),
                    },
                    "escalation": {
                        "id": str(escalation.id),
                        "status": escalation.status,
                        "priority": escalation.priority,
                        "rule_id": escalation.rule_id,
                        "assigned_staff_id": str(escalation.assigned_staff_id),
                    },
                    "audit_actions": [f"{e.actor_type}:{e.action}" for e in audit],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
