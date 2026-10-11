import threading
import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain import appointment_service, intake_service
from app.domain.actors import Actor
from app.domain.errors import (
    AlreadyBooked,
    DomainError,
    IdempotencyConflict,
    InvalidTransition,
    NotFound,
    SlotUnavailable,
    ValidationFailed,
)
from app.models import Appointment, AuditEvent, AvailabilitySlot, IntakeRecord, StaffMember
from app.models.enums import AppointmentStatus, FieldStatus, IntakeStatus, SlotStatus
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm

AGENT = Actor.agent()


def new_intake(session: Session, firm: SeededFirm, category: str | None = None) -> IntakeRecord:
    intake = intake_service.create_draft_intake(session, firm_id=firm.firm_id, actor=AGENT, now=NOW)
    if category:
        intake_service.save_intake_answer(
            session,
            firm_id=firm.firm_id,
            intake_id=intake.id,
            actor=AGENT,
            field_key="matter_category",
            status=FieldStatus.REPORTED_BY_CALLER,
            value=category,
            now=NOW,
        )
    return intake


def book(
    session: Session, firm: SeededFirm, intake_id: uuid.UUID, slot_id: uuid.UUID, key: str
) -> appointment_service.BookingResult:
    return appointment_service.book_consultation(
        session,
        firm_id=firm.firm_id,
        actor=AGENT,
        intake_id=intake_id,
        slot_id=slot_id,
        idempotency_key=key,
        now=NOW,
    )


def first_slot(session: Session, firm: SeededFirm, category: str = "EMPLOYMENT") -> uuid.UUID:
    slots = appointment_service.check_availability(
        session, firm_id=firm.firm_id, matter_category=category, now=NOW
    )
    return slots[0].id


def test_availability_is_window_bounded_sorted_and_lawyers_only(
    session: Session, demo: SeededFirm
) -> None:
    slots = appointment_service.check_availability(session, firm_id=demo.firm_id, limit=50, now=NOW)
    assert slots, "seeded calendar should offer slots"
    assert [s.starts_at for s in slots] == sorted(s.starts_at for s in slots)
    assert all(NOW + timedelta(hours=2) <= s.starts_at <= NOW + timedelta(days=14) for s in slots)
    assert all(s.status is SlotStatus.AVAILABLE for s in slots)
    lawyers = {demo.staff("aarav"), demo.staff("meera"), demo.staff("daniel")}
    assert {s.staff_id for s in slots} <= lawyers


def test_availability_excludes_short_notice_far_future_and_blocked_slots(
    session: Session, demo: SeededFirm
) -> None:
    lawyer = demo.staff("aarav")
    too_soon = AvailabilitySlot(
        firm_id=demo.firm_id,
        staff_id=lawyer,
        starts_at=NOW + timedelta(hours=1),
        ends_at=NOW + timedelta(hours=1, minutes=30),
    )
    too_far = AvailabilitySlot(
        firm_id=demo.firm_id,
        staff_id=lawyer,
        starts_at=NOW + timedelta(days=20),
        ends_at=NOW + timedelta(days=20, minutes=30),
    )
    session.add_all([too_soon, too_far])
    blocked = session.get_one(AvailabilitySlot, first_slot(session, demo))
    blocked.status = SlotStatus.BLOCKED
    session.flush()

    ids = {
        s.id
        for s in appointment_service.check_availability(
            session, firm_id=demo.firm_id, limit=50, now=NOW
        )
    }
    assert not ids & {too_soon.id, too_far.id, blocked.id}


def test_availability_filters_by_practice_area(session: Session, demo: SeededFirm) -> None:
    family = appointment_service.check_availability(
        session, firm_id=demo.firm_id, matter_category="FAMILY", limit=50, now=NOW
    )
    assert {s.staff_id for s in family} == {demo.staff("meera")}
    assert (
        appointment_service.check_availability(
            session, firm_id=demo.firm_id, matter_category="OTHER", now=NOW
        )
        == []
    )
    with pytest.raises(ValidationFailed):
        appointment_service.check_availability(
            session, firm_id=demo.firm_id, matter_category="CRIMINAL", now=NOW
        )


def test_inactive_lawyer_slots_are_not_offered(session: Session, demo: SeededFirm) -> None:
    session.get_one(StaffMember, demo.staff("meera")).is_active = False
    session.flush()
    assert (
        appointment_service.check_availability(
            session, firm_id=demo.firm_id, matter_category="FAMILY", now=NOW
        )
        == []
    )


def test_booking_persists_appointment_and_marks_slot(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo, "EMPLOYMENT")
    slot_id = first_slot(session, demo)
    result = book(session, demo, intake.id, slot_id, "book-key-0001")
    assert not result.replayed

    session.expire_all()
    verified = appointment_service.get_appointment(
        session, firm_id=demo.firm_id, appointment_id=result.appointment.id
    )
    slot = session.get_one(AvailabilitySlot, slot_id)
    assert verified.status is AppointmentStatus.BOOKED
    assert (verified.intake_id, verified.slot_id, verified.staff_id) == (
        intake.id,
        slot_id,
        slot.staff_id,
    )
    assert (verified.starts_at, verified.ends_at) == (slot.starts_at, slot.ends_at)
    assert slot.status is SlotStatus.BOOKED
    assert session.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_id == verified.id)
    ).all() == ["appointment.booked"]


def test_retry_with_same_key_returns_same_appointment(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo, "EMPLOYMENT")
    slot_id = first_slot(session, demo)
    first = book(session, demo, intake.id, slot_id, "book-key-0002")
    retry = book(session, demo, intake.id, slot_id, "book-key-0002")
    assert retry.replayed
    assert retry.appointment.id == first.appointment.id
    assert session.scalar(select(func.count()).select_from(Appointment)) == 1


def test_same_key_for_different_request_conflicts(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo, "EMPLOYMENT")
    slots = appointment_service.check_availability(
        session, firm_id=demo.firm_id, matter_category="EMPLOYMENT", now=NOW
    )
    book(session, demo, intake.id, slots[0].id, "book-key-0003")
    with pytest.raises(IdempotencyConflict):
        book(session, demo, intake.id, slots[1].id, "book-key-0003")


def test_booked_slot_cannot_be_booked_again(session: Session, demo: SeededFirm) -> None:
    slot_id = first_slot(session, demo)
    book(session, demo, new_intake(session, demo, "EMPLOYMENT").id, slot_id, "book-key-0004")
    with pytest.raises(SlotUnavailable):
        book(session, demo, new_intake(session, demo, "EMPLOYMENT").id, slot_id, "book-key-0005")


def test_one_consultation_per_intake(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo, "EMPLOYMENT")
    slots = appointment_service.check_availability(
        session, firm_id=demo.firm_id, matter_category="EMPLOYMENT", now=NOW
    )
    book(session, demo, intake.id, slots[0].id, "book-key-0006")
    with pytest.raises(AlreadyBooked):
        book(session, demo, intake.id, slots[1].id, "book-key-0007")


def test_lawyer_must_handle_the_matter_category(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo, "FAMILY")
    employment_only_slot = next(
        s
        for s in appointment_service.check_availability(
            session, firm_id=demo.firm_id, limit=50, now=NOW
        )
        if s.staff_id == demo.staff("aarav")
    )
    with pytest.raises(SlotUnavailable, match="category"):
        book(session, demo, intake.id, employment_only_slot.id, "book-key-0008")


def test_slot_outside_booking_window_is_rejected(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo)
    slot = AvailabilitySlot(
        firm_id=demo.firm_id,
        staff_id=demo.staff("aarav"),
        starts_at=NOW + timedelta(minutes=30),
        ends_at=NOW + timedelta(minutes=60),
    )
    session.add(slot)
    session.flush()
    with pytest.raises(SlotUnavailable, match="window"):
        book(session, demo, intake.id, slot.id, "book-key-0009")


def test_closed_intake_cannot_book(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo)
    intake.status = IntakeStatus.CLOSED
    session.flush()
    with pytest.raises(InvalidTransition):
        book(session, demo, intake.id, first_slot(session, demo), "book-key-0010")


def test_invalid_idempotency_key_rejected(session: Session, demo: SeededFirm) -> None:
    intake = new_intake(session, demo)
    with pytest.raises(ValidationFailed):
        book(session, demo, intake.id, first_slot(session, demo), "short")


def test_cross_tenant_booking_is_impossible(
    session: Session, demo: SeededFirm, other_firm: SeededFirm
) -> None:
    my_intake = new_intake(session, demo)
    their_slot = first_slot(session, other_firm)
    with pytest.raises(NotFound):
        book(session, demo, my_intake.id, their_slot, "book-key-0011")
    with pytest.raises(NotFound):
        book(session, other_firm, my_intake.id, their_slot, "book-key-0012")


def test_database_rejects_double_booking_even_if_service_is_bypassed(
    session: Session, demo: SeededFirm
) -> None:
    slot_id = first_slot(session, demo)
    booked = book(
        session, demo, new_intake(session, demo, "EMPLOYMENT").id, slot_id, "book-key-0013"
    ).appointment
    other = new_intake(session, demo)
    session.add(
        Appointment(
            firm_id=demo.firm_id,
            intake_id=other.id,
            slot_id=slot_id,
            staff_id=booked.staff_id,
            starts_at=booked.starts_at,
            ends_at=booked.ends_at,
            status=AppointmentStatus.BOOKED,
            idempotency_key="book-key-raw-insert",
        )
    )
    with pytest.raises(IntegrityError, match="uq_appointments_slot_booked"):
        session.flush()


def _run_concurrently(n: int, target: Callable[[int], object]) -> list[object]:
    barrier = threading.Barrier(n)
    results: list[object] = [None] * n

    def worker(i: int) -> None:
        barrier.wait()
        try:
            results[i] = target(i)
        except Exception as exc:  # collected and asserted by the caller
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_concurrent_bookings_of_one_slot_yield_exactly_one_appointment(
    committed_demo: tuple[sessionmaker[Session], SeededFirm],
) -> None:
    factory, demo = committed_demo
    with factory() as s, s.begin():
        intake_ids = [new_intake(s, demo, "EMPLOYMENT").id for _ in range(5)]
        slot_id = first_slot(s, demo)

    def attempt(i: int) -> uuid.UUID:
        with factory() as s, s.begin():
            return book(s, demo, intake_ids[i], slot_id, f"race-key-{i:04d}").appointment.id

    results = _run_concurrently(5, attempt)
    successes = [r for r in results if isinstance(r, uuid.UUID)]
    failures = [r for r in results if isinstance(r, DomainError)]
    assert len(successes) == 1, results
    assert len(failures) == 4 and all(isinstance(f, SlotUnavailable) for f in failures), results
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(Appointment)) == 1


def test_concurrent_retries_with_same_key_create_one_appointment(
    committed_demo: tuple[sessionmaker[Session], SeededFirm],
) -> None:
    factory, demo = committed_demo
    with factory() as s, s.begin():
        intake_id = new_intake(s, demo, "EMPLOYMENT").id
        slot_id = first_slot(s, demo)

    def attempt(_: int) -> appointment_service.BookingResult:
        with factory() as s, s.begin():
            return book(s, demo, intake_id, slot_id, "same-retry-key")

    results = _run_concurrently(4, attempt)
    assert all(isinstance(r, appointment_service.BookingResult) for r in results), results
    booking_results = [r for r in results if isinstance(r, appointment_service.BookingResult)]
    assert len({r.appointment.id for r in booking_results}) == 1
    assert sum(not r.replayed for r in booking_results) == 1
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(Appointment)) == 1
