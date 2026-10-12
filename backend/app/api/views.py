"""ORM rows -> API response models. Each view reads persisted state; nothing is inferred."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import (
    appointment_service,
    escalation_service,
    intake_service,
    notification_service,
    review_service,
    transcript_service,
    workflow_service,
)
from app.domain.errors import NotFound
from app.domain.firm_config_service import get_active_firm, load_config
from app.models import Escalation, IntakeRecord, StaffMember, WorkflowExecution
from app.schemas.api import (
    AppointmentOut,
    AuditOut,
    CallOut,
    EscalationOut,
    FieldOut,
    IntakeDetailOut,
    NotificationOut,
    PriorValueOut,
    TurnOut,
    WorkflowOut,
)


def appointment_out(item: review_service.AppointmentListItem) -> AppointmentOut:
    a = item.appointment
    return AppointmentOut(
        id=a.id,
        intake_id=a.intake_id,
        intake_reference=item.intake_reference,
        lawyer=item.lawyer,
        starts_at=a.starts_at,
        ends_at=a.ends_at,
        status=a.status,
    )


def _handoff_workflow(session: Session, escalation_id: uuid.UUID) -> WorkflowOut | None:
    try:
        execution = workflow_service.get_execution(
            session, workflow_id=workflow_service.handoff_workflow_id(escalation_id)
        )
    except NotFound:
        return None
    return WorkflowOut.model_validate(execution)


def escalation_out(
    session: Session,
    escalation: Escalation,
    intake_reference: str,
    assigned_to: str | None,
    *,
    detail: bool = True,
) -> EscalationOut:
    out = EscalationOut(
        id=escalation.id,
        intake_id=escalation.intake_id,
        intake_reference=intake_reference,
        reason_code=escalation.reason_code,
        rule_id=escalation.rule_id,
        reason_text=escalation.reason_text,
        priority=escalation.priority,
        status=escalation.status,
        assigned_to=assigned_to,
        resolution_note=escalation.resolution_note,
        last_error=escalation.last_error,
        created_at=escalation.created_at,
        accepted_at=escalation.accepted_at,
        resolved_at=escalation.resolved_at,
    )
    if detail:
        out.notifications = [
            NotificationOut.model_validate(n)
            for n in notification_service.list_notifications(
                session, firm_id=escalation.firm_id, escalation_id=escalation.id
            )
        ]
        out.handoff_workflow = _handoff_workflow(session, escalation.id)
    return out


def escalation_detail(
    session: Session, firm_id: uuid.UUID, escalation_id: uuid.UUID
) -> EscalationOut:
    escalation = escalation_service.get_escalation(
        session, firm_id=firm_id, escalation_id=escalation_id
    )
    reference = session.scalar(
        select(IntakeRecord.reference).where(IntakeRecord.id == escalation.intake_id)
    )
    assigned = (
        session.scalar(
            select(StaffMember.full_name).where(StaffMember.id == escalation.assigned_staff_id)
        )
        if escalation.assigned_staff_id
        else None
    )
    return escalation_out(session, escalation, reference or "", assigned)


def intake_detail(session: Session, firm_id: uuid.UUID, intake_id: uuid.UUID) -> IntakeDetailOut:
    intake = intake_service.get_intake(session, firm_id=firm_id, intake_id=intake_id)
    config = load_config(get_active_firm(session, firm_id))
    stored = intake_service.intake_fields(intake)
    fields = []
    for question in config.intake_questions:
        entry = stored.get(question.key)
        if entry is None:
            continue
        fields.append(
            FieldOut(
                key=question.key,
                prompt=question.prompt,
                required=question.required,
                value=entry.value,
                status=entry.status,
                note=entry.note,
                source=entry.source,
                updated_at=entry.updated_at,
                history=[PriorValueOut.model_validate(h) for h in entry.history],
            )
        )
    booked = appointment_service.get_booked_appointment_for_intake(
        session, firm_id=firm_id, intake_id=intake.id
    )
    appointment = (
        appointment_out(
            review_service.get_appointment_item(session, firm_id=firm_id, appointment_id=booked.id)
        )
        if booked
        else None
    )
    calls = [
        CallOut(
            id=call.id,
            channel=call.channel,
            status=call.status,
            end_reason=call.end_reason,
            started_at=call.started_at,
            ended_at=call.ended_at,
            consent=call.consent,
            turns=[
                TurnOut.model_validate(t)
                for t in transcript_service.list_turns(
                    session, firm_id=firm_id, call_session_id=call.id
                )
            ],
        )
        for call in review_service.intake_call_sessions(
            session, firm_id=firm_id, intake_id=intake.id
        )
    ]
    escalations = [
        escalation_detail(session, firm_id, e.id)
        for e in escalation_service.list_escalations(session, firm_id=firm_id, intake_id=intake.id)
    ]
    return IntakeDetailOut(
        id=intake.id,
        reference=intake.reference,
        status=intake.status,
        summary=intake.summary,
        outstanding=intake.outstanding,
        created_at=intake.created_at,
        updated_at=intake.updated_at,
        fields=fields,
        escalations=escalations,
        appointment=appointment,
        calls=calls,
        workflows=[
            WorkflowOut.model_validate(w)
            for w in review_service.intake_workflows(session, firm_id=firm_id, intake_id=intake.id)
        ],
        timeline=[
            AuditOut.model_validate(e)
            for e in review_service.intake_timeline(session, firm_id=firm_id, intake_id=intake.id)
        ],
    )


def workflow_out(execution: WorkflowExecution) -> WorkflowOut:
    return WorkflowOut.model_validate(execution)
