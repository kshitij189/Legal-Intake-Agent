"""Staff API (blueprint §12, review side). Thin routes over domain services: every query is scoped
to the signed-in staff member's firm, and every write goes through the same authorization,
state-machine and audit code as the agent. Writes commit explicitly."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.api import views
from app.api.deps import REVIEWER_ROLES, SESSION_COOKIE, Db, Staff
from app.api.throttle import LoginThrottle
from app.config import get_settings
from app.domain import (
    escalation_service,
    intake_service,
    notification_service,
    review_service,
    staff_auth_service,
)
from app.domain.errors import ValidationFailed
from app.domain.firm_config_service import get_active_firm
from app.models import Escalation
from app.models.enums import EscalationStatus, FieldStatus, IntakeStatus
from app.schemas.api import (
    AppointmentOut,
    EscalationOut,
    FieldUpdateIn,
    IntakeDetailOut,
    IntakeListOut,
    LoginIn,
    NotificationOut,
    ResolveIn,
    StaffOut,
    WorkflowOut,
)

router = APIRouter(prefix="/api/v1")

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


def _staff_out(staff: Staff) -> StaffOut:
    s = staff.staff
    return StaffOut(
        id=s.id,
        full_name=s.full_name,
        email=s.email,
        role=s.role,
        firm_name=staff.firm.name,
        firm_slug=staff.firm.slug,
        firm_timezone=staff.firm.timezone,
        can_review=staff.can_review,
    )


# --- auth -----------------------------------------------------------------------------------


@router.post("/auth/login")
def login(body: LoginIn, db: Db, response: Response, request: Request) -> StaffOut:
    # Keyed by account, not client IP: behind the dashboard proxy every request comes from
    # 127.0.0.1, and a forwarded-for header could be spoofed to dodge the limit.
    throttle: LoginThrottle = request.app.state.login_throttle
    key = (body.firm.strip().lower(), body.email.strip().lower())
    throttle.check(key)
    try:
        token, member = staff_auth_service.login(
            db, firm_slug=body.firm, email=body.email, password=body.password
        )
    except staff_auth_service.AuthenticationFailed:
        throttle.failed(key)
        raise
    throttle.succeeded(key)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(staff_auth_service.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=get_settings().dashboard_cookie_secure,
        path="/",
    )
    firm = get_active_firm(db, member.firm_id)
    return StaffOut(
        id=member.id,
        full_name=member.full_name,
        email=member.email,
        role=member.role,
        firm_name=firm.name,
        firm_slug=firm.slug,
        firm_timezone=firm.timezone,
        can_review=member.role in REVIEWER_ROLES,
    )


@router.post("/auth/logout", status_code=204)
def logout(staff: Staff, db: Db, response: Response) -> None:
    staff_auth_service.logout(db, staff.token)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/me")
def me(staff: Staff) -> StaffOut:
    return _staff_out(staff)


# --- intakes --------------------------------------------------------------------------------


@router.get("/intakes")
def list_intakes(
    staff: Staff,
    db: Db,
    status: Annotated[list[IntakeStatus] | None, Query()] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[IntakeListOut]:
    return [
        IntakeListOut(
            id=item.intake.id,
            reference=item.intake.reference,
            status=item.intake.status,
            caller_name=item.caller_name,
            matter_category=item.matter_category,
            open_escalations=item.open_escalations,
            appointment_starts_at=item.appointment_starts_at,
            created_at=item.intake.created_at,
            updated_at=item.intake.updated_at,
        )
        for item in review_service.list_intakes(
            db, firm_id=staff.firm_id, statuses=status, limit=limit, offset=offset
        )
    ]


@router.get("/intakes/{intake_id}")
def get_intake(intake_id: uuid.UUID, staff: Staff, db: Db) -> IntakeDetailOut:
    return views.intake_detail(db, staff.firm_id, intake_id)


@router.patch("/intakes/{intake_id}/fields/{field_key}")
def update_field(
    intake_id: uuid.UUID, field_key: str, body: FieldUpdateIn, staff: Staff, db: Db
) -> IntakeDetailOut:
    """Staff correction or verification of one field (VERIFIED_BY_SYSTEM: reviewers only). The
    firm's escalation rules are re-applied to the corrected record, as for the agent."""
    if body.status is FieldStatus.MISSING:
        raise ValidationFailed("a field cannot be reset to MISSING")
    intake_service.save_intake_answer(
        db,
        firm_id=staff.firm_id,
        intake_id=intake_id,
        actor=staff.actor,
        field_key=field_key,
        status=body.status,
        value=body.value,
        note=body.note,
    )
    escalation_service.apply_escalation_rules(
        db, firm_id=staff.firm_id, intake_id=intake_id, actor=staff.actor
    )
    db.commit()
    return views.intake_detail(db, staff.firm_id, intake_id)


@router.post("/intakes/{intake_id}/close")
def close_intake(intake_id: uuid.UUID, staff: Staff, db: Db) -> IntakeDetailOut:
    intake_service.transition_intake(
        db,
        firm_id=staff.firm_id,
        intake_id=intake_id,
        actor=staff.actor,
        target=IntakeStatus.CLOSED,
    )
    db.commit()
    return views.intake_detail(db, staff.firm_id, intake_id)


# --- escalations ----------------------------------------------------------------------------


@router.get("/escalations")
def escalation_queue(
    staff: Staff,
    db: Db,
    status: Annotated[list[EscalationStatus] | None, Query()] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[EscalationOut]:
    return [
        views.escalation_out(db, item.escalation, item.intake_reference, item.assigned_to)
        for item in review_service.list_escalation_queue(
            db, firm_id=staff.firm_id, statuses=status, limit=limit, offset=offset
        )
    ]


@router.get("/escalations/{escalation_id}")
def get_escalation(escalation_id: uuid.UUID, staff: Staff, db: Db) -> EscalationOut:
    return views.escalation_detail(db, staff.firm_id, escalation_id)


def _after_status_change(request: Request, escalation: Escalation) -> None:
    """The handoff workflow is told after commit; it re-reads the database either way."""
    request.app.state.handoff_signal(escalation.id)


@router.post("/escalations/{escalation_id}/accept")
def accept_escalation(
    escalation_id: uuid.UUID, staff: Staff, db: Db, request: Request
) -> EscalationOut:
    escalation = escalation_service.accept_escalation(
        db, firm_id=staff.firm_id, escalation_id=escalation_id, actor=staff.actor
    )
    db.commit()
    _after_status_change(request, escalation)
    return views.escalation_detail(db, staff.firm_id, escalation_id)


@router.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(
    escalation_id: uuid.UUID, body: ResolveIn, staff: Staff, db: Db, request: Request
) -> EscalationOut:
    escalation = escalation_service.resolve_escalation(
        db,
        firm_id=staff.firm_id,
        escalation_id=escalation_id,
        actor=staff.actor,
        resolution_note=body.note,
    )
    db.commit()
    _after_status_change(request, escalation)
    return views.escalation_detail(db, staff.firm_id, escalation_id)


# --- appointments, workflows, notifications ---------------------------------------------------


@router.get("/appointments")
def list_appointments(
    staff: Staff, db: Db, upcoming: bool = False, limit: Limit = 100, offset: Offset = 0
) -> list[AppointmentOut]:
    since = datetime.now(UTC) if upcoming else None
    return [
        views.appointment_out(item)
        for item in review_service.list_appointments(
            db, firm_id=staff.firm_id, from_time=since, limit=limit, offset=offset
        )
    ]


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: uuid.UUID, staff: Staff, db: Db) -> AppointmentOut:
    return views.appointment_out(
        review_service.get_appointment_item(
            db, firm_id=staff.firm_id, appointment_id=appointment_id
        )
    )


@router.get("/workflows")
def list_workflows(
    staff: Staff, db: Db, limit: Limit = 100, offset: Offset = 0
) -> list[WorkflowOut]:
    return [
        views.workflow_out(w)
        for w in review_service.list_workflows(
            db, firm_id=staff.firm_id, limit=limit, offset=offset
        )
    ]


@router.get("/notifications")
def list_notifications(staff: Staff, db: Db) -> list[NotificationOut]:
    references = review_service.escalation_references(db, firm_id=staff.firm_id)
    out = []
    for n in reversed(notification_service.list_notifications(db, firm_id=staff.firm_id)):
        item = NotificationOut.model_validate(n)
        item.intake_reference = references.get(n.escalation_id)
        out.append(item)
    return out


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: uuid.UUID, staff: Staff, db: Db) -> NotificationOut:
    notification = notification_service.mark_read(
        db, firm_id=staff.firm_id, notification_id=notification_id
    )
    db.commit()
    return NotificationOut.model_validate(notification)
