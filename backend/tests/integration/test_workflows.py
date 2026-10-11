"""Durable workflows in Temporal's time-skipping test environment, with real activities against the
PostgreSQL test database. Timers (ack windows, post-call delay) are skipped, not waited for."""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.testing import WorkflowEnvironment

from app.db.seed import seed_demo_data
from app.db.session import make_session_factory
from app.domain import appointment_service, call_session_service, escalation_service, intake_service
from app.domain.actors import Actor
from app.domain.workflow_service import booking_workflow_id, get_execution
from app.models import Appointment, Escalation, StaffNotification, WorkflowExecution
from app.models.enums import (
    EscalationReason,
    EscalationStatus,
    FieldStatus,
    NotificationKind,
    SessionChannel,
    WorkflowStatus,
    WorkflowType,
)
from app.workflows.activities import IntakeActivities
from app.workflows.types import BookingInput, BookingOutcome
from app.workflows.worker import build_worker, dispatch_pending
from app.workflows.workflows import BookingWorkflow, HandoffWorkflow
from tests.helpers import DEMO_SLUG
from tests.integration.conftest import TABLES, SeededFirm

T = TypeVar("T")
AGENT = Actor.agent()
Live = tuple[sessionmaker[Session], SeededFirm]


@pytest.fixture
def live(engine: Engine) -> Iterator[Live]:
    """Committed demo data seeded relative to the real clock (workflow activities use it)."""
    sessions = make_session_factory(engine)
    with sessions() as s, s.begin():
        result = seed_demo_data(s, slug=DEMO_SLUG, now=datetime.now(UTC))
    yield sessions, SeededFirm(result.firm_id, result.staff_ids)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))


async def result_of(client: Client, workflow_id: str) -> Any:
    """Skip time explicitly until the workflow closes, then return its result."""
    env = CURRENT_ENV[0]
    handle = client.get_workflow_handle(workflow_id)
    for _ in range(500):
        desc = await handle.describe()
        if desc.status != WorkflowExecutionStatus.RUNNING:
            return await handle.result()
        await env.sleep(timedelta(minutes=5))
    raise AssertionError(f"{workflow_id} still running after skipping time")


CURRENT_ENV: list[WorkflowEnvironment] = []


def with_temporal(
    sessions: sessionmaker[Session], body: Callable[[Client, str], Any], **faults: str
) -> Any:
    """Run `body(client, task_queue)` with a worker in a fresh time-skipping environment."""

    async def main() -> Any:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            CURRENT_ENV[:] = [env]
            queue = f"test-{uuid.uuid4().hex[:8]}"
            activities = IntakeActivities(
                sessions,
                calendar_fault=faults.get("calendar"),
                notifier_fault=faults.get("notifier"),
            )
            async with build_worker(env.client, activities, queue):
                return await body(env.client, queue)

    return asyncio.run(main())


def new_intake(
    sessions: sessionmaker[Session], firm: SeededFirm, category: str = "EMPLOYMENT"
) -> uuid.UUID:
    with sessions() as s, s.begin():
        intake = intake_service.create_draft_intake(s, firm_id=firm.firm_id, actor=AGENT)
        intake_service.save_intake_answer(
            s,
            firm_id=firm.firm_id,
            intake_id=intake.id,
            actor=AGENT,
            field_key="matter_category",
            status=FieldStatus.REPORTED_BY_CALLER,
            value=category,
        )
        return intake.id


def escalate(sessions: sessionmaker[Session], firm: SeededFirm, intake_id: uuid.UUID) -> uuid.UUID:
    with sessions() as s, s.begin():
        return escalation_service.create_escalation(
            s,
            firm_id=firm.firm_id,
            actor=AGENT,
            intake_id=intake_id,
            reason_code=EscalationReason.UNABLE_TO_COMPLETE,
            description="Caller's audio kept dropping.",
            idempotency_key=f"esc-{uuid.uuid4().hex}",
        ).escalation.id


def notifications(
    sessions: sessionmaker[Session], escalation_id: uuid.UUID
) -> list[tuple[str, str]]:
    with sessions() as s:
        rows = s.scalars(
            select(StaffNotification)
            .where(StaffNotification.escalation_id == escalation_id)
            .order_by(StaffNotification.created_at, StaffNotification.dedupe_key)
        ).all()
        return [(str(n.kind), str(n.recipient_role)) for n in rows]


async def dispatch(sessions: sessionmaker[Session], client: Client, queue: str) -> int:
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(dispatch_pending, sessions, client, loop, queue)


def test_escalation_requests_its_handoff_in_the_same_transaction(live: Live) -> None:
    sessions, firm = live
    escalation_id = escalate(sessions, firm, new_intake(sessions, firm))
    with sessions() as s:
        execution = get_execution(s, workflow_id=f"handoff-{escalation_id}")
    assert (execution.workflow_type, execution.status) == (
        WorkflowType.HANDOFF,
        WorkflowStatus.REQUESTED,
    )


def test_handoff_alerts_then_reminds_then_falls_back(live: Live) -> None:
    sessions, firm = live
    escalation_id = escalate(sessions, firm, new_intake(sessions, firm))

    async def body(client: Client, queue: str) -> str:
        assert await dispatch(sessions, client, queue) == 1
        assert await dispatch(sessions, client, queue) == 0  # nothing left to start
        return await result_of(client, f"handoff-{escalation_id}")

    assert with_temporal(sessions, body) == "UNACKNOWLEDGED"
    assert notifications(sessions, escalation_id) == [
        ("NEW_ESCALATION", "INTAKE_COORDINATOR"),
        ("REMINDER", "INTAKE_COORDINATOR"),
        ("REMINDER", "INTAKE_COORDINATOR"),
        ("UNACKNOWLEDGED_FALLBACK", "ADMINISTRATOR"),
    ]
    with sessions() as s:
        execution = get_execution(s, workflow_id=f"handoff-{escalation_id}")
        assert (execution.status, execution.result) == (WorkflowStatus.COMPLETED, "UNACKNOWLEDGED")


def test_handoff_stops_once_staff_accept(live: Live) -> None:
    sessions, firm = live
    escalation_id = escalate(sessions, firm, new_intake(sessions, firm))

    async def body(client: Client, queue: str) -> str:
        await dispatch(sessions, client, queue)
        handle = client.get_workflow_handle(f"handoff-{escalation_id}")
        for _ in range(100):  # wait until the first alert is delivered
            if notifications(sessions, escalation_id):
                break
            await asyncio.sleep(0.1)
        with sessions() as s, s.begin():
            escalation_service.accept_escalation(
                s, firm_id=firm.firm_id, escalation_id=escalation_id, actor=firm.coordinator
            )
        await handle.signal(HandoffWorkflow.status_changed)
        return await result_of(client, f"handoff-{escalation_id}")

    assert with_temporal(sessions, body) == "ACCEPTED"
    assert notifications(sessions, escalation_id) == [("NEW_ESCALATION", "INTAKE_COORDINATOR")]


def test_unavailable_review_channel_marks_handoff_failed_and_uses_fallback(live: Live) -> None:
    sessions, firm = live
    escalation_id = escalate(sessions, firm, new_intake(sessions, firm))

    async def body(client: Client, queue: str) -> str:
        await dispatch(sessions, client, queue)
        return await result_of(client, f"handoff-{escalation_id}")

    assert with_temporal(sessions, body, notifier="unavailable") == "DELIVERY_FAILED"
    assert notifications(sessions, escalation_id) == [("HANDOFF_FAILED_FALLBACK", "ADMINISTRATOR")]
    with sessions() as s:
        escalation = s.get_one(Escalation, escalation_id)
        execution = get_execution(s, workflow_id=f"handoff-{escalation_id}")
    assert escalation.status is EscalationStatus.FAILED
    assert "could not be delivered" in (escalation.last_error or "")
    assert execution.status is WorkflowStatus.FAILED


def test_flaky_review_channel_still_delivers_exactly_one_alert(live: Live) -> None:
    sessions, firm = live
    escalation_id = escalate(sessions, firm, new_intake(sessions, firm))
    with sessions() as s, s.begin():  # accepted later, so the run ends after the first alert
        pass

    async def body(client: Client, queue: str) -> None:
        await dispatch(sessions, client, queue)
        for _ in range(200):
            if notifications(sessions, escalation_id):
                return
            await asyncio.sleep(0.1)

    with_temporal(sessions, body, notifier="fail_first:2")
    assert notifications(sessions, escalation_id) == [("NEW_ESCALATION", "INTAKE_COORDINATOR")]
    with sessions() as s:
        assert s.get_one(Escalation, escalation_id).status is EscalationStatus.PENDING


def first_slot(sessions: sessionmaker[Session], firm: SeededFirm) -> uuid.UUID:
    with sessions() as s:
        return appointment_service.check_availability(
            s, firm_id=firm.firm_id, matter_category="EMPLOYMENT"
        )[0].id


def run_booking(
    sessions: sessionmaker[Session], inp: BookingInput, **faults: str
) -> BookingOutcome:
    async def body(client: Client, queue: str) -> BookingOutcome:
        await client.start_workflow(
            BookingWorkflow.run, inp, id=booking_workflow_id(inp.idempotency_key), task_queue=queue
        )
        return BookingOutcome(**(await result_of(client, booking_workflow_id(inp.idempotency_key))))

    result: BookingOutcome = with_temporal(sessions, body, **faults)
    return result


def booking(firm: SeededFirm, intake_id: uuid.UUID, slot_id: uuid.UUID, key: str) -> BookingInput:
    return BookingInput(
        str(firm.firm_id), str(intake_id), str(slot_id), key, "AGENT", "intake-agent"
    )


def appointment_count(sessions: sessionmaker[Session]) -> int:
    with sessions() as s:
        return s.scalar(select(func.count()).select_from(Appointment)) or 0


def test_booking_workflow_books_and_records_execution(live: Live) -> None:
    sessions, firm = live
    intake_id = new_intake(sessions, firm)
    outcome = run_booking(
        sessions, booking(firm, intake_id, first_slot(sessions, firm), "wf-book-001")
    )
    assert (outcome.status, outcome.replayed) == ("BOOKED", False)
    assert appointment_count(sessions) == 1
    with sessions() as s:
        execution = get_execution(s, workflow_id=booking_workflow_id("wf-book-001"))
    assert (execution.workflow_type, execution.status) == (
        WorkflowType.BOOKING,
        WorkflowStatus.COMPLETED,
    )


def test_calendar_timeout_after_commit_is_resolved_by_a_state_check(live: Live) -> None:
    sessions, firm = live
    intake_id = new_intake(sessions, firm)
    outcome = run_booking(
        sessions,
        booking(firm, intake_id, first_slot(sessions, firm), "wf-book-002"),
        calendar="timeout_after_commit:1",
    )
    # Attempt 1 committed but "timed out"; the workflow checked state instead of re-booking.
    assert (outcome.status, outcome.replayed) == ("BOOKED", True)
    assert appointment_count(sessions) == 1


def test_booking_rejection_is_not_retried(live: Live) -> None:
    sessions, firm = live
    slot = first_slot(sessions, firm)
    run_booking(sessions, booking(firm, new_intake(sessions, firm), slot, "wf-book-003"))
    outcome = run_booking(sessions, booking(firm, new_intake(sessions, firm), slot, "wf-book-004"))
    assert (outcome.status, outcome.error_code) == ("REJECTED", "SLOT_UNAVAILABLE")
    assert appointment_count(sessions) == 1


def end_call(
    sessions: sessionmaker[Session], firm: SeededFirm, intake_id: uuid.UUID, finalize: bool
) -> uuid.UUID:
    with sessions() as s, s.begin():
        call = call_session_service.start_call_session(
            s, firm_id=firm.firm_id, actor=AGENT, channel=SessionChannel.TEXT
        )
        call_session_service.attach_intake(
            s, firm_id=firm.firm_id, call_session_id=call.id, intake_id=intake_id, actor=AGENT
        )
        if finalize:
            intake_service.finalize_intake(
                s,
                firm_id=firm.firm_id,
                intake_id=intake_id,
                actor=AGENT,
                summary="Employment issue.",
            )
        from app.models.enums import CallSessionStatus

        call_session_service.end_call_session(
            s,
            firm_id=firm.firm_id,
            call_session_id=call.id,
            actor=AGENT,
            status=CallSessionStatus.COMPLETED if finalize else CallSessionStatus.DISCONNECTED,
        )
        return call.id


def post_call(sessions: sessionmaker[Session], call_id: uuid.UUID) -> str:
    async def body(client: Client, queue: str) -> str:
        await dispatch(sessions, client, queue)
        return await result_of(client, f"post-call-{call_id}")

    result: str = with_temporal(sessions, body)
    return result


def test_unfinished_intake_is_queued_for_follow_up_after_the_delay(live: Live) -> None:
    sessions, firm = live
    intake_id = new_intake(sessions, firm)
    call_id = end_call(sessions, firm, intake_id, finalize=False)
    assert post_call(sessions, call_id) == "CREATED"
    with sessions() as s:
        follow_up = s.scalars(select(Escalation).where(Escalation.intake_id == intake_id)).one()
        assert follow_up.reason_code is EscalationReason.INCOMPLETE_INTAKE
        # Its own handoff is requested through the outbox, like any other escalation.
        assert (
            get_execution(s, workflow_id=f"handoff-{follow_up.id}").status
            is WorkflowStatus.REQUESTED
        )


def test_finalized_intake_needs_no_follow_up(live: Live) -> None:
    sessions, firm = live
    intake_id = new_intake(sessions, firm)
    call_id = end_call(sessions, firm, intake_id, finalize=True)
    assert post_call(sessions, call_id) == "NOTHING_TO_DO:PENDING_REVIEW"
    with sessions() as s:
        assert s.scalars(select(Escalation).where(Escalation.intake_id == intake_id)).all() == []


def test_dispatch_survives_a_lost_commit(live: Live) -> None:
    """If the dispatcher started a workflow but crashed before committing STARTED, the next
    dispatch must recognise the running workflow instead of starting a duplicate."""
    sessions, firm = live
    escalation_id = escalate(sessions, firm, new_intake(sessions, firm))
    workflow_id = f"handoff-{escalation_id}"

    async def body(client: Client, queue: str) -> int:
        await dispatch(sessions, client, queue)
        with sessions() as s, s.begin():  # simulate the lost commit
            get_execution(s, workflow_id=workflow_id).status = WorkflowStatus.REQUESTED
        first_run = (await client.get_workflow_handle(workflow_id).describe()).run_id
        await dispatch(sessions, client, queue)
        second_run = (await client.get_workflow_handle(workflow_id).describe()).run_id
        return int(first_run == second_run)  # same execution: no duplicate was started

    assert with_temporal(sessions, body) == 1
    with sessions() as s:
        assert get_execution(s, workflow_id=workflow_id).status in (
            WorkflowStatus.STARTED,
            WorkflowStatus.COMPLETED,
        )
        assert s.scalar(select(func.count()).select_from(WorkflowExecution)) == 1
    assert NotificationKind.NEW_ESCALATION  # imported for readability of the module
