"""Consultation availability and idempotent booking.

Booking order: lock intake -> idempotency replay check -> one-booking-per-intake check ->
lock slot -> re-validate slot -> write. Partial unique indexes back up the checks.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.actors import Action, Actor, authorize
from app.domain.audit import record_audit
from app.domain.clock import resolve_now
from app.domain.errors import (
    AlreadyBooked,
    IdempotencyConflict,
    InvalidTransition,
    NotFound,
    SlotUnavailable,
    ValidationFailed,
)
from app.domain.firm_config_service import get_active_firm, load_config
from app.domain.idempotency import validate_idempotency_key
from app.domain.intake_service import get_intake, intake_fields
from app.models import Appointment, AvailabilitySlot, StaffMember
from app.models.enums import AppointmentStatus, IntakeStatus, SlotStatus, StaffRole
from app.schemas.firm_config import MATTER_CATEGORY_KEY, FirmConfig

ENTITY = "appointment"
MAX_AVAILABILITY_RESULTS = 50


@dataclass(frozen=True)
class BookingResult:
    appointment: Appointment
    replayed: bool  # True when an earlier request with the same idempotency key already booked it


def _booking_window(config: FirmConfig, now: datetime) -> tuple[datetime, datetime]:
    policy = config.scheduling
    return (
        now + timedelta(hours=policy.min_notice_hours),
        now + timedelta(days=policy.max_days_ahead),
    )


def check_availability(
    session: Session,
    *,
    firm_id: uuid.UUID,
    matter_category: str | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> list[AvailabilitySlot]:
    """Read-only: open consultation slots of active lawyers within the firm's booking window."""
    now = resolve_now(now)
    config = load_config(get_active_firm(session, firm_id))
    earliest, latest = _booking_window(config, now)
    limit = max(1, min(limit, MAX_AVAILABILITY_RESULTS))

    stmt = (
        select(AvailabilitySlot)
        .join(
            StaffMember,
            (StaffMember.id == AvailabilitySlot.staff_id)
            & (StaffMember.firm_id == AvailabilitySlot.firm_id),
        )
        .where(
            AvailabilitySlot.firm_id == firm_id,
            AvailabilitySlot.status == SlotStatus.AVAILABLE,
            AvailabilitySlot.starts_at >= earliest,
            AvailabilitySlot.starts_at <= latest,
            StaffMember.is_active.is_(True),
            StaffMember.role == StaffRole.LAWYER,
        )
        .order_by(AvailabilitySlot.starts_at, AvailabilitySlot.id)
        .limit(limit)
    )
    if matter_category is not None:
        codes = {c.code for c in config.matter_categories}
        if matter_category not in codes:
            raise ValidationFailed("unknown matter category", allowed=sorted(codes))
        stmt = stmt.where(StaffMember.practice_areas.contains([matter_category]))
    return list(session.scalars(stmt))


def book_consultation(
    session: Session,
    *,
    firm_id: uuid.UUID,
    actor: Actor,
    intake_id: uuid.UUID,
    slot_id: uuid.UUID,
    idempotency_key: str,
    now: datetime | None = None,
) -> BookingResult:
    now = resolve_now(now)
    validate_idempotency_key(idempotency_key)
    authorize(session, firm_id, actor, Action.APPOINTMENT_BOOK)
    config = load_config(get_active_firm(session, firm_id))

    # Locking the intake serializes all bookings for it, so the replay check below is reliable.
    intake = get_intake(session, firm_id=firm_id, intake_id=intake_id, for_update=True)

    existing = session.scalar(
        select(Appointment).where(
            Appointment.firm_id == firm_id, Appointment.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if existing.intake_id == intake_id and existing.slot_id == slot_id:
            return BookingResult(existing, replayed=True)
        raise IdempotencyConflict("idempotency key was already used for a different booking")

    if intake.status is IntakeStatus.CLOSED:
        raise InvalidTransition("a closed intake cannot be booked", current=str(intake.status))
    booked = session.scalar(
        select(Appointment).where(
            Appointment.intake_id == intake_id, Appointment.status == AppointmentStatus.BOOKED
        )
    )
    if booked is not None:
        raise AlreadyBooked(
            "this intake already has a booked consultation", appointment_id=str(booked.id)
        )

    slot = session.scalar(
        select(AvailabilitySlot)
        .where(AvailabilitySlot.id == slot_id, AvailabilitySlot.firm_id == firm_id)
        .with_for_update()
    )
    if slot is None:
        raise NotFound("slot not found", slot_id=str(slot_id))
    if slot.status is not SlotStatus.AVAILABLE:
        raise SlotUnavailable("slot is no longer available", slot_id=str(slot_id))
    earliest, latest = _booking_window(config, now)
    if not earliest <= slot.starts_at <= latest:
        raise SlotUnavailable("slot is outside the firm's booking window", slot_id=str(slot_id))

    lawyer = session.scalar(
        select(StaffMember).where(StaffMember.id == slot.staff_id, StaffMember.firm_id == firm_id)
    )
    if lawyer is None or not lawyer.is_active or lawyer.role is not StaffRole.LAWYER:
        raise SlotUnavailable("slot owner is not an active lawyer", slot_id=str(slot_id))
    category = intake_fields(intake).get(MATTER_CATEGORY_KEY)
    if category and category.value and category.value not in lawyer.practice_areas:
        raise SlotUnavailable(
            "slot's lawyer does not handle this matter category", slot_id=str(slot_id)
        )

    appointment = Appointment(
        firm_id=firm_id,
        intake_id=intake_id,
        slot_id=slot.id,
        staff_id=slot.staff_id,
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        status=AppointmentStatus.BOOKED,
        idempotency_key=idempotency_key,
    )
    slot.status = SlotStatus.BOOKED
    session.add(appointment)
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="appointment.booked",
        entity_type=ENTITY,
        entity_id=appointment.id,
        details={
            "intake_id": str(intake_id),
            "slot_id": str(slot.id),
            "staff_id": str(slot.staff_id),
            "starts_at": slot.starts_at.isoformat(),
        },
    )
    return BookingResult(appointment, replayed=False)


def find_appointment_by_key(
    session: Session, *, firm_id: uuid.UUID, idempotency_key: str
) -> Appointment | None:
    """State check used before retrying a booking whose outcome is unknown (e.g. timeout)."""
    return session.scalar(
        select(Appointment).where(
            Appointment.firm_id == firm_id, Appointment.idempotency_key == idempotency_key
        )
    )


def get_booked_appointment_for_intake(
    session: Session, *, firm_id: uuid.UUID, intake_id: uuid.UUID
) -> Appointment | None:
    return session.scalar(
        select(Appointment).where(
            Appointment.firm_id == firm_id,
            Appointment.intake_id == intake_id,
            Appointment.status == AppointmentStatus.BOOKED,
        )
    )


def get_appointment(
    session: Session, *, firm_id: uuid.UUID, appointment_id: uuid.UUID
) -> Appointment:
    """Authoritative read used to verify a booking before confirming it to a caller."""
    appointment = session.scalar(
        select(Appointment).where(Appointment.id == appointment_id, Appointment.firm_id == firm_id)
    )
    if appointment is None:
        raise NotFound("appointment not found", appointment_id=str(appointment_id))
    return appointment
