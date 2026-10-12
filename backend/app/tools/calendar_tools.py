"""Calendar tools: offer real slots only, book idempotently, confirm only after re-reading."""

import logging
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain import appointment_service, intake_service
from app.domain.errors import (
    AlreadyBooked,
    DomainError,
    IdempotencyConflict,
    InvalidTransition,
    NotFound,
    PermissionDenied,
    SlotUnavailable,
    ValidationFailed,
)
from app.domain.firm_config_service import get_active_firm
from app.models import StaffMember
from app.models.enums import AppointmentStatus
from app.schemas.firm_config import MATTER_CATEGORY_KEY
from app.tools.context import SlotNotOffered, ToolContext, VerificationFailed, require_intake
from app.tools.schemas import BookConsultationInput, CheckAvailabilityInput
from app.workflows.client import TemporalUnavailable, run_booking_workflow
from app.workflows.types import BOOKED, REJECTED, BookingInput

logger = logging.getLogger(__name__)


def booking_key(ctx: ToolContext, slot_id: object) -> str:
    """Deterministic, so a repeated tool call can never create a second appointment."""
    return f"book:{ctx.call_session_id}:{slot_id}"


def _local(dt: datetime, timezone: str) -> str:
    return dt.astimezone(ZoneInfo(timezone)).strftime("%A %d %B %Y, %H:%M %Z")


def check_appointment_availability(
    session: Session, ctx: ToolContext, args: CheckAvailabilityInput
) -> dict[str, Any]:
    category = args.matter_category
    if category is None and ctx.intake_id is not None:
        intake = intake_service.get_intake(session, firm_id=ctx.firm_id, intake_id=ctx.intake_id)
        category = intake_service.intake_fields(intake)[MATTER_CATEGORY_KEY].value
    slots = appointment_service.check_availability(
        session, firm_id=ctx.firm_id, matter_category=category, limit=args.limit, now=ctx.clock()
    )
    timezone = get_active_firm(session, ctx.firm_id).timezone
    lawyers = {s.staff_id: session.get_one(StaffMember, s.staff_id).full_name for s in slots}
    ctx.offered_slot_ids.update(s.id for s in slots)
    return {
        "matter_category": category,
        "slots": [
            {
                "slot_id": str(s.id),
                "starts_at": _local(s.starts_at, timezone),
                "lawyer": lawyers[s.staff_id],
            }
            for s in slots
        ],
        "note": None if slots else "No suitable slots are available; offer a callback instead.",
    }


def book_consultation(
    session: Session, ctx: ToolContext, args: BookConsultationInput
) -> dict[str, Any]:
    intake_id = require_intake(ctx)
    if args.slot_id not in ctx.offered_slot_ids:
        raise SlotNotOffered(
            "only slots returned by check_appointment_availability in this conversation can be"
            " booked"
        )
    key = booking_key(ctx, args.slot_id)
    if ctx.durable_booking:
        outcome = _book_via_workflow(ctx, intake_id, args.slot_id, key)
        if outcome is not None:
            return outcome
    result = appointment_service.book_consultation(
        session,
        firm_id=ctx.firm_id,
        actor=ctx.actor,
        intake_id=intake_id,
        slot_id=args.slot_id,
        idempotency_key=key,
        now=ctx.clock(),
    )
    return {"appointment_id": str(result.appointment.id), "replayed": result.replayed}


_REJECTIONS: dict[str, type[DomainError]] = {
    error.code: error
    for error in (
        SlotUnavailable,
        AlreadyBooked,
        IdempotencyConflict,
        NotFound,
        ValidationFailed,
        InvalidTransition,
        PermissionDenied,
    )
}


class BookingNotConfirmed(DomainError):
    code = "BOOKING_NOT_CONFIRMED"


def _book_via_workflow(
    ctx: ToolContext, intake_id: uuid.UUID, slot_id: uuid.UUID, key: str
) -> dict[str, Any] | None:
    """Durable booking (Temporal). Returns None only if Temporal itself is unreachable, in which
    case the caller books directly - same idempotency key, same verification."""
    settings = get_settings()
    if ctx.booking_task_queue:
        settings = settings.model_copy(update={"temporal_task_queue": ctx.booking_task_queue})
    request = BookingInput(
        firm_id=str(ctx.firm_id),
        intake_id=str(intake_id),
        slot_id=str(slot_id),
        idempotency_key=key,
        actor_type=str(ctx.actor.type),
        actor_id=ctx.actor.id,
    )
    try:
        outcome = run_booking_workflow(settings, request, settings.booking_wait_seconds)
    except TemporalUnavailable:
        logger.warning("booking_workflow_unavailable_direct_fallback")
        return None
    if outcome is None:
        raise BookingNotConfirmed(
            "the booking is still being confirmed; do not tell the caller it is booked"
        )
    if outcome.status == BOOKED and outcome.appointment_id:
        return {"appointment_id": outcome.appointment_id, "replayed": outcome.replayed}
    if outcome.status == REJECTED:
        raise _REJECTIONS.get(outcome.error_code or "", DomainError)(
            outcome.message or "the booking was rejected"
        )
    raise BookingNotConfirmed(
        "the calendar could not confirm the booking; offer a callback instead",
        reason=outcome.message,
    )


def verify_booking(session: Session, ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    """Runs after commit in a fresh session: only a stored BOOKED appointment is confirmed."""
    appointment = appointment_service.get_appointment(
        session, firm_id=ctx.firm_id, appointment_id=uuid.UUID(data["appointment_id"])
    )
    if appointment.status is not AppointmentStatus.BOOKED:
        raise VerificationFailed("the appointment could not be verified as booked")
    timezone = get_active_firm(session, ctx.firm_id).timezone
    return {
        **data,
        "verified": True,
        "status": appointment.status,
        "starts_at": _local(appointment.starts_at, timezone),
        "lawyer": session.get_one(StaffMember, appointment.staff_id).full_name,
    }
