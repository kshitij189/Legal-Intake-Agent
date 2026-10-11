"""Temporal activities: each runs one unit of work against the domain services in its own
transaction. They are idempotent, so Temporal can retry them (or re-run them after a worker crash)
without duplicating side effects.

Business rejections are raised as non-retryable `ApplicationError`s carrying the domain code.
Infrastructure failures propagate and are retried by the calling workflow's retry policy.
"""

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.domain import (
    appointment_service,
    escalation_service,
    intake_service,
    notification_service,
    workflow_service,
)
from app.domain.actors import Actor
from app.domain.call_session_service import get_call_session
from app.domain.errors import DomainError
from app.domain.firm_config_service import get_active_firm, load_config
from app.models.enums import (
    ActorType,
    EscalationReason,
    EscalationStatus,
    IntakeStatus,
    NotificationKind,
    StaffRole,
    WorkflowType,
)
from app.workflows.faults import CALENDAR_MODES, NOTIFIER_MODES, parse_fault
from app.workflows.types import (
    BOOKED,
    BookingInput,
    BookingOutcome,
    ExecutionRef,
    ExecutionResult,
    HandoffFailure,
    HandoffInput,
    HandoffPlan,
    NotifyInput,
    PostCallInput,
    PostCallPlan,
)

logger = logging.getLogger(__name__)

SYSTEM = Actor.system("workflow-worker")
HANG_SECONDS = 120


def incomplete_follow_up_key(intake_id: uuid.UUID) -> str:
    return f"incomplete:{intake_id}"


class IntakeActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        calendar_fault: str | None = None,
        notifier_fault: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._calendar_fault = parse_fault(calendar_fault, CALENDAR_MODES)
        self._notifier_fault = parse_fault(notifier_fault, NOTIFIER_MODES)
        for name, fault in (("calendar", self._calendar_fault), ("notifier", self._notifier_fault)):
            if fault is not None:
                logger.warning("FAULT INJECTION ENABLED for %s: %s", name, fault)

    # --- execution bookkeeping -----------------------------------------------------------

    @activity.defn
    def begin_execution(self, ref: ExecutionRef) -> None:
        with self._sessions() as session, session.begin():
            workflow_service.request_workflow(
                session,
                firm_id=uuid.UUID(ref.firm_id),
                workflow_id=ref.workflow_id,
                workflow_type=WorkflowType(ref.workflow_type),
                entity_type=ref.entity_type,
                entity_id=uuid.UUID(ref.entity_id),
            )
            workflow_service.mark_started(session, workflow_id=ref.workflow_id)

    @activity.defn
    def finish_execution(self, result: ExecutionResult) -> None:
        with self._sessions() as session, session.begin():
            workflow_service.mark_finished(
                session,
                workflow_id=result.workflow_id,
                succeeded=result.succeeded,
                result=result.result,
                error=result.error,
            )

    # --- handoff -------------------------------------------------------------------------

    @activity.defn
    def plan_handoff(self, inp: HandoffInput) -> HandoffPlan:
        firm_id = uuid.UUID(inp.firm_id)
        with self._sessions() as session:
            policy = load_config(get_active_firm(session, firm_id)).handoff
            escalation = escalation_service.get_escalation(
                session, firm_id=firm_id, escalation_id=uuid.UUID(inp.escalation_id)
            )
            return HandoffPlan(
                priority=str(escalation.priority),
                reason_text=escalation.reason_text,
                ack_minutes=policy.ack_minutes(escalation.priority),
                max_reminders=policy.max_reminders,
                delivery_attempts=policy.delivery_attempts,
                reviewer_role=str(policy.reviewer_role),
                fallback_role=str(policy.fallback_role),
                status=str(escalation.status),
            )

    @activity.defn
    def notify_reviewers(self, inp: NotifyInput) -> bool:
        """Deliver one alert. Idempotent per dedupe key; returns True if newly created."""
        fault = self._notifier_fault
        if (
            inp.via_reviewer_channel
            and fault is not None
            and fault.applies_to_attempt(activity.info().attempt)
        ):
            raise RuntimeError("simulated: reviewer notification channel unavailable")
        with self._sessions() as session, session.begin():
            _, created = notification_service.notify(
                session,
                firm_id=uuid.UUID(inp.firm_id),
                escalation_id=uuid.UUID(inp.escalation_id),
                recipient_role=StaffRole(inp.recipient_role),
                kind=NotificationKind(inp.kind),
                dedupe_key=inp.dedupe_key,
                message=inp.message,
            )
        return created

    @activity.defn
    def escalation_status(self, inp: HandoffInput) -> str:
        with self._sessions() as session:
            return str(
                escalation_service.get_escalation(
                    session,
                    firm_id=uuid.UUID(inp.firm_id),
                    escalation_id=uuid.UUID(inp.escalation_id),
                ).status
            )

    @activity.defn
    def mark_handoff_failed(self, inp: HandoffFailure) -> None:
        """Delivery failed for good: record FAILED and alert the fallback role on the dashboard
        (a separate channel from the failing reviewer notifier)."""
        firm_id, escalation_id = uuid.UUID(inp.firm_id), uuid.UUID(inp.escalation_id)
        with self._sessions() as session, session.begin():
            escalation = escalation_service.get_escalation(
                session, firm_id=firm_id, escalation_id=escalation_id, for_update=True
            )
            if escalation.status is EscalationStatus.PENDING:
                escalation_service.mark_escalation_failed(
                    session,
                    firm_id=firm_id,
                    escalation_id=escalation_id,
                    actor=SYSTEM,
                    error=f"Reviewer alert could not be delivered: {inp.error}"[:900],
                )
            notification_service.notify(
                session,
                firm_id=firm_id,
                escalation_id=escalation_id,
                recipient_role=StaffRole(inp.fallback_role),
                kind=NotificationKind.HANDOFF_FAILED_FALLBACK,
                dedupe_key=f"handoff:{escalation_id}:failed",
                message="Reviewer alerts could not be delivered for this escalation. "
                "Please assign it manually.",
            )

    # --- booking -------------------------------------------------------------------------

    @activity.defn
    def book_slot(self, inp: BookingInput) -> BookingOutcome:
        firm_id = uuid.UUID(inp.firm_id)
        actor = Actor(ActorType(inp.actor_type), inp.actor_id)
        try:
            with self._sessions() as session, session.begin():
                result = appointment_service.book_consultation(
                    session,
                    firm_id=firm_id,
                    actor=actor,
                    intake_id=uuid.UUID(inp.intake_id),
                    slot_id=uuid.UUID(inp.slot_id),
                    idempotency_key=inp.idempotency_key,
                )
                outcome = BookingOutcome(
                    status=BOOKED,
                    appointment_id=str(result.appointment.id),
                    replayed=result.replayed,
                )
        except DomainError as exc:
            raise ApplicationError(exc.message, type=exc.code, non_retryable=True) from exc
        fault = self._calendar_fault
        if fault is not None and fault.applies_to_attempt(inp.attempt):
            if fault.mode == "timeout_after_commit":
                raise RuntimeError("simulated: calendar timed out after the booking committed")
            if fault.mode == "hang_after_commit":
                time.sleep(HANG_SECONDS)  # the worker is killed or the activity times out
        return outcome

    @activity.defn
    def find_booking(self, inp: BookingInput) -> BookingOutcome:
        with self._sessions() as session:
            appointment = appointment_service.find_appointment_by_key(
                session, firm_id=uuid.UUID(inp.firm_id), idempotency_key=inp.idempotency_key
            )
        if appointment is None:
            return BookingOutcome(status="NOT_FOUND")
        return BookingOutcome(status=BOOKED, appointment_id=str(appointment.id), replayed=True)

    # --- post-call -----------------------------------------------------------------------

    @activity.defn
    def plan_post_call(self, inp: PostCallInput) -> PostCallPlan:
        firm_id = uuid.UUID(inp.firm_id)
        with self._sessions() as session:
            call = get_call_session(
                session, firm_id=firm_id, call_session_id=uuid.UUID(inp.call_session_id)
            )
            policy = load_config(get_active_firm(session, firm_id)).handoff
            if call.intake_id is None:
                return PostCallPlan(inp.firm_id, None, None, policy.incomplete_follow_up_minutes)
            intake = intake_service.get_intake(session, firm_id=firm_id, intake_id=call.intake_id)
            return PostCallPlan(
                inp.firm_id, str(intake.id), str(intake.status), policy.incomplete_follow_up_minutes
            )

    @activity.defn
    def create_incomplete_follow_up(self, inp: PostCallPlan) -> str:
        """If the intake is still INCOMPLETE, queue it for staff follow-up (idempotent)."""
        if inp.intake_id is None:
            return "NO_INTAKE"
        firm_id, intake_id = uuid.UUID(inp.firm_id), uuid.UUID(inp.intake_id)
        with self._sessions() as session, session.begin():
            intake = intake_service.get_intake(session, firm_id=firm_id, intake_id=intake_id)
            if intake.status is not IntakeStatus.INCOMPLETE:
                return f"NOT_NEEDED:{intake.status}"
            result = escalation_service.create_escalation(
                session,
                firm_id=firm_id,
                actor=SYSTEM,
                intake_id=intake_id,
                reason_code=EscalationReason.INCOMPLETE_INTAKE,
                description="The caller ended the conversation before the intake was finished. "
                "Please follow up using the details captured so far.",
                idempotency_key=incomplete_follow_up_key(intake_id),
            )
            return "CREATED" if result.created else "ALREADY_QUEUED"

    def all(self) -> list[Callable[..., Any]]:
        return [
            self.begin_execution,
            self.finish_execution,
            self.plan_handoff,
            self.notify_reviewers,
            self.escalation_status,
            self.mark_handoff_failed,
            self.book_slot,
            self.find_booking,
            self.plan_post_call,
            self.create_incomplete_follow_up,
        ]
