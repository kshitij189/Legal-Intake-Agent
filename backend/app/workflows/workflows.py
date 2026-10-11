"""Durable workflows. Deterministic orchestration only: every side effect is an idempotent activity,
so Temporal can replay this code after a worker restart without repeating work.
"""

import contextlib
from dataclasses import replace
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import IntakeActivities
    from app.workflows.types import (
        BOOKED,
        FAILED,
        REJECTED,
        BookingInput,
        BookingOutcome,
        ExecutionRef,
        ExecutionResult,
        HandoffFailure,
        HandoffInput,
        NotifyInput,
        PostCallInput,
    )

SHORT = timedelta(seconds=30)
DB_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5)
BOOKING_ACTIVITY_TIMEOUT = timedelta(seconds=10)
MAX_BOOKING_ATTEMPTS = 3


async def _begin(firm_id: str, workflow_type: str, entity_type: str, entity_id: str) -> str:
    workflow_id = workflow.info().workflow_id
    await workflow.execute_activity_method(
        IntakeActivities.begin_execution,
        ExecutionRef(workflow_id, firm_id, workflow_type, entity_type, entity_id),
        start_to_close_timeout=SHORT,
        retry_policy=DB_RETRY,
    )
    return workflow_id


async def _finish(workflow_id: str, succeeded: bool, result: str, error: str | None = None) -> None:
    await workflow.execute_activity_method(
        IntakeActivities.finish_execution,
        ExecutionResult(workflow_id, succeeded, result, error),
        start_to_close_timeout=SHORT,
        retry_policy=DB_RETRY,
    )


def _cause(err: ActivityError) -> str:
    return str(err.cause or err)


@workflow.defn
class HandoffWorkflow:
    """Alert reviewers about a queued escalation; remind them until someone accepts it; fall back
    to another role when alerts cannot be delivered or nobody responds."""

    def __init__(self) -> None:
        self._status_changed = False

    @workflow.signal
    def status_changed(self) -> None:
        """Optional nudge from the dashboard (Phase 7); the database stays authoritative."""
        self._status_changed = True

    @workflow.run
    async def run(self, inp: HandoffInput) -> str:
        workflow_id = await _begin(inp.firm_id, "HANDOFF", "escalation", inp.escalation_id)
        plan = await workflow.execute_activity_method(
            IntakeActivities.plan_handoff,
            inp,
            start_to_close_timeout=SHORT,
            retry_policy=DB_RETRY,
        )
        if plan.status != "PENDING":
            await _finish(workflow_id, True, f"ALREADY_{plan.status}")
            return f"ALREADY_{plan.status}"

        delivery = RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=60),
            maximum_attempts=plan.delivery_attempts,
        )
        try:
            await workflow.execute_activity_method(
                IntakeActivities.notify_reviewers,
                NotifyInput(
                    inp.firm_id,
                    inp.escalation_id,
                    plan.reviewer_role,
                    "NEW_ESCALATION",
                    f"handoff:{inp.escalation_id}:new",
                    f"New {plan.priority} escalation: {plan.reason_text}",
                ),
                start_to_close_timeout=SHORT,
                retry_policy=delivery,
            )
        except ActivityError as err:
            await workflow.execute_activity_method(
                IntakeActivities.mark_handoff_failed,
                HandoffFailure(inp.firm_id, inp.escalation_id, plan.fallback_role, _cause(err)),
                start_to_close_timeout=SHORT,
                retry_policy=DB_RETRY,
            )
            await _finish(workflow_id, False, "DELIVERY_FAILED", _cause(err))
            return "DELIVERY_FAILED"

        reminders = 0
        while True:
            self._status_changed = False
            with contextlib.suppress(TimeoutError):
                await workflow.wait_condition(
                    lambda: self._status_changed, timeout=timedelta(minutes=plan.ack_minutes)
                )
            status = await workflow.execute_activity_method(
                IntakeActivities.escalation_status,
                inp,
                start_to_close_timeout=SHORT,
                retry_policy=DB_RETRY,
            )
            if status != "PENDING":
                result = status
                break
            if reminders >= plan.max_reminders:
                await self._alert(
                    inp,
                    plan.fallback_role,
                    "UNACKNOWLEDGED_FALLBACK",
                    f"handoff:{inp.escalation_id}:unacknowledged",
                    f"Nobody has accepted this {plan.priority} escalation yet: {plan.reason_text}",
                    delivery,
                    via_reviewer_channel=False,
                )
                result = "UNACKNOWLEDGED"
                break
            reminders += 1
            await self._alert(
                inp,
                plan.reviewer_role,
                "REMINDER",
                f"handoff:{inp.escalation_id}:reminder:{reminders}",
                f"Reminder ({reminders}): {plan.priority} escalation awaiting acceptance.",
                delivery,
            )
        await _finish(workflow_id, True, result)
        return result

    async def _alert(
        self,
        inp: HandoffInput,
        role: str,
        kind: str,
        key: str,
        message: str,
        retry: RetryPolicy,
        *,
        via_reviewer_channel: bool = True,
    ) -> None:
        """Reminders are best effort: a failed reminder does not end the handoff."""
        try:
            await workflow.execute_activity_method(
                IntakeActivities.notify_reviewers,
                NotifyInput(
                    inp.firm_id, inp.escalation_id, role, kind, key, message, via_reviewer_channel
                ),
                start_to_close_timeout=SHORT,
                retry_policy=retry,
            )
        except ActivityError:
            workflow.logger.warning("handoff alert not delivered: %s", key)


@workflow.defn
class BookingWorkflow:
    """Book a consultation. On a timeout or transient failure, check the actual booking state
    before retrying (blueprint §10); the idempotency key makes every attempt safe."""

    @workflow.run
    async def run(self, inp: BookingInput) -> BookingOutcome:
        workflow_id = await _begin(inp.firm_id, "BOOKING", "intake", inp.intake_id)
        outcome = BookingOutcome(
            status=FAILED, error_code="CALENDAR_UNAVAILABLE", message="the calendar did not respond"
        )
        for attempt in range(1, MAX_BOOKING_ATTEMPTS + 1):
            request = replace(inp, attempt=attempt)
            try:
                outcome = await workflow.execute_activity_method(
                    IntakeActivities.book_slot,
                    request,
                    start_to_close_timeout=BOOKING_ACTIVITY_TIMEOUT,
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                break
            except ActivityError as err:
                cause = err.cause
                if isinstance(cause, ApplicationError) and cause.non_retryable:
                    outcome = BookingOutcome(
                        status=REJECTED, error_code=cause.type, message=cause.message
                    )
                    break
                found = await workflow.execute_activity_method(
                    IntakeActivities.find_booking,
                    request,
                    start_to_close_timeout=SHORT,
                    retry_policy=DB_RETRY,
                )
                if found.status == BOOKED:
                    outcome = found
                    break
                outcome = BookingOutcome(
                    status=FAILED, error_code="CALENDAR_UNAVAILABLE", message=_cause(err)
                )
                if attempt < MAX_BOOKING_ATTEMPTS:
                    await workflow.sleep(timedelta(seconds=2**attempt))
        await _finish(workflow_id, outcome.status == BOOKED, outcome.status, outcome.message)
        return outcome


@workflow.defn
class PostCallWorkflow:
    """After a call: if the intake was left INCOMPLETE, wait the firm's follow-up delay and, if it
    is still incomplete, queue it for staff follow-up (which starts its own handoff)."""

    @workflow.run
    async def run(self, inp: PostCallInput) -> str:
        workflow_id = await _begin(inp.firm_id, "POST_CALL", "call_session", inp.call_session_id)
        plan = await workflow.execute_activity_method(
            IntakeActivities.plan_post_call,
            inp,
            start_to_close_timeout=SHORT,
            retry_policy=DB_RETRY,
        )
        if plan.intake_status != "INCOMPLETE":
            result = f"NOTHING_TO_DO:{plan.intake_status}"
        else:
            await workflow.sleep(timedelta(minutes=plan.follow_up_minutes))
            result = await workflow.execute_activity_method(
                IntakeActivities.create_incomplete_follow_up,
                plan,
                start_to_close_timeout=SHORT,
                retry_policy=DB_RETRY,
            )
        await _finish(workflow_id, True, result)
        return result


WORKFLOWS = [HandoffWorkflow, BookingWorkflow, PostCallWorkflow]
