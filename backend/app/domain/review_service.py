"""Read-side queries for the human-review dashboard. Every query is scoped by `firm_id`; the
dashboard shows persisted state only, never a cached or derived claim about a handoff."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.domain import intake_service
from app.domain.errors import NotFound
from app.models import (
    Appointment,
    AuditEvent,
    CallSession,
    Escalation,
    IntakeRecord,
    StaffMember,
    WorkflowExecution,
)
from app.models.enums import AppointmentStatus, EscalationStatus, IntakeStatus, Priority

OPEN_ESCALATION = (EscalationStatus.PENDING, EscalationStatus.ACCEPTED, EscalationStatus.FAILED)


@dataclass(frozen=True)
class IntakeListItem:
    intake: IntakeRecord
    caller_name: str | None
    matter_category: str | None
    open_escalations: int
    appointment_starts_at: datetime | None


def list_intakes(
    session: Session,
    *,
    firm_id: uuid.UUID,
    statuses: list[IntakeStatus] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[IntakeListItem]:
    open_count = (
        select(func.count())
        .select_from(Escalation)
        .where(Escalation.intake_id == IntakeRecord.id, Escalation.status.in_(OPEN_ESCALATION))
        .scalar_subquery()
    )
    booked = (
        select(Appointment.starts_at)
        .where(
            Appointment.intake_id == IntakeRecord.id,
            Appointment.status == AppointmentStatus.BOOKED,
        )
        .scalar_subquery()
    )
    stmt = select(IntakeRecord, open_count, booked).where(IntakeRecord.firm_id == firm_id)
    if statuses:
        stmt = stmt.where(IntakeRecord.status.in_(statuses))
    rows = session.execute(
        stmt.order_by(IntakeRecord.created_at.desc()).limit(limit).offset(offset)
    ).all()
    items = []
    for intake, escalations, starts_at in rows:
        fields = intake_service.intake_fields(intake)
        name, category = fields.get("caller_name"), fields.get("matter_category")
        items.append(
            IntakeListItem(
                intake=intake,
                caller_name=name.value if name else None,
                matter_category=category.value if category else None,
                open_escalations=escalations or 0,
                appointment_starts_at=starts_at,
            )
        )
    return items


def intake_call_sessions(
    session: Session, *, firm_id: uuid.UUID, intake_id: uuid.UUID
) -> list[CallSession]:
    return list(
        session.scalars(
            select(CallSession)
            .where(CallSession.firm_id == firm_id, CallSession.intake_id == intake_id)
            .order_by(CallSession.started_at)
        )
    )


def intake_timeline(
    session: Session, *, firm_id: uuid.UUID, intake_id: uuid.UUID
) -> list[AuditEvent]:
    """The intake's whole action history: the intake, its escalations, appointments and call
    sessions, in commit order (`seq`)."""
    intake_service.get_intake(session, firm_id=firm_id, intake_id=intake_id)  # 404 across firms
    related = [
        intake_id,
        *session.scalars(select(Escalation.id).where(Escalation.intake_id == intake_id)),
        *session.scalars(select(Appointment.id).where(Appointment.intake_id == intake_id)),
        *session.scalars(select(CallSession.id).where(CallSession.intake_id == intake_id)),
    ]
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.firm_id == firm_id, AuditEvent.entity_id.in_(related))
            .order_by(AuditEvent.seq)
        )
    )


def intake_workflows(
    session: Session, *, firm_id: uuid.UUID, intake_id: uuid.UUID
) -> list[WorkflowExecution]:
    escalations = select(Escalation.id).where(Escalation.intake_id == intake_id)
    calls = select(CallSession.id).where(CallSession.intake_id == intake_id)
    return list(
        session.scalars(
            select(WorkflowExecution)
            .where(
                WorkflowExecution.firm_id == firm_id,
                or_(
                    WorkflowExecution.entity_id == intake_id,
                    WorkflowExecution.entity_id.in_(escalations),
                    WorkflowExecution.entity_id.in_(calls),
                ),
            )
            .order_by(WorkflowExecution.created_at)
        )
    )


@dataclass(frozen=True)
class EscalationListItem:
    escalation: Escalation
    intake_reference: str
    assigned_to: str | None


def list_escalation_queue(
    session: Session,
    *,
    firm_id: uuid.UUID,
    statuses: list[EscalationStatus] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[EscalationListItem]:
    """Open work first (FAILED and URGENT at the top), then newest."""
    status_rank = case(
        {
            EscalationStatus.FAILED: 0,
            EscalationStatus.PENDING: 1,
            EscalationStatus.ACCEPTED: 2,
            EscalationStatus.RESOLVED: 3,
        },
        value=Escalation.status,
    )
    priority_rank = case(
        {Priority.URGENT: 0, Priority.HIGH: 1, Priority.NORMAL: 2}, value=Escalation.priority
    )
    stmt = (
        select(Escalation, IntakeRecord.reference, StaffMember.full_name)
        .join(IntakeRecord, IntakeRecord.id == Escalation.intake_id)
        .outerjoin(StaffMember, StaffMember.id == Escalation.assigned_staff_id)
        .where(Escalation.firm_id == firm_id)
    )
    if statuses:
        stmt = stmt.where(Escalation.status.in_(statuses))
    stmt = (
        stmt.order_by(status_rank, priority_rank, Escalation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [EscalationListItem(e, ref, name) for e, ref, name in session.execute(stmt).all()]


@dataclass(frozen=True)
class AppointmentListItem:
    appointment: Appointment
    intake_reference: str
    lawyer: str


def list_appointments(
    session: Session,
    *,
    firm_id: uuid.UUID,
    from_time: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AppointmentListItem]:
    stmt = (
        select(Appointment, IntakeRecord.reference, StaffMember.full_name)
        .join(IntakeRecord, IntakeRecord.id == Appointment.intake_id)
        .join(StaffMember, StaffMember.id == Appointment.staff_id)
        .where(Appointment.firm_id == firm_id)
    )
    if from_time is not None:
        stmt = stmt.where(Appointment.starts_at >= from_time)
    rows = session.execute(stmt.order_by(Appointment.starts_at).limit(limit).offset(offset)).all()
    return [AppointmentListItem(a, ref, lawyer) for a, ref, lawyer in rows]


def get_appointment_item(
    session: Session, *, firm_id: uuid.UUID, appointment_id: uuid.UUID
) -> AppointmentListItem:
    row = session.execute(
        select(Appointment, IntakeRecord.reference, StaffMember.full_name)
        .join(IntakeRecord, IntakeRecord.id == Appointment.intake_id)
        .join(StaffMember, StaffMember.id == Appointment.staff_id)
        .where(Appointment.firm_id == firm_id, Appointment.id == appointment_id)
    ).one_or_none()
    if row is None:
        raise NotFound("appointment not found", appointment_id=str(appointment_id))
    return AppointmentListItem(*row)


def list_workflows(
    session: Session, *, firm_id: uuid.UUID, limit: int = 100, offset: int = 0
) -> list[WorkflowExecution]:
    return list(
        session.scalars(
            select(WorkflowExecution)
            .where(WorkflowExecution.firm_id == firm_id)
            .order_by(WorkflowExecution.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )


def escalation_references(session: Session, *, firm_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """Escalation id -> intake reference, for labelling notifications."""
    rows = session.execute(
        select(Escalation.id, IntakeRecord.reference)
        .join(IntakeRecord, IntakeRecord.id == Escalation.intake_id)
        .where(Escalation.firm_id == firm_id)
    ).tuples()
    return dict(rows.all())


def actor_names(session: Session, *, firm_id: uuid.UUID, staff_ids: set[str]) -> dict[str, str]:
    """Staff actor id -> full name, for timelines (agent/system actors keep their component id)."""
    ids = []
    for value in staff_ids:
        try:
            ids.append(uuid.UUID(value))
        except ValueError:
            continue
    if not ids:
        return {}
    rows = session.execute(
        select(StaffMember.id, StaffMember.full_name).where(
            StaffMember.firm_id == firm_id, StaffMember.id.in_(ids)
        )
    ).tuples()
    return {str(staff_id): name for staff_id, name in rows}
