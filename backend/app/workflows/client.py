"""Temporal calls from synchronous code: the in-call booking workflow and the dashboard's
handoff signal."""

import asyncio
import logging
import uuid

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import Settings
from app.domain.workflow_service import booking_workflow_id, handoff_workflow_id
from app.workflows.types import BookingInput, BookingOutcome
from app.workflows.workflows import BookingWorkflow, HandoffWorkflow

logger = logging.getLogger(__name__)


class TemporalUnavailable(Exception):
    pass


async def _run(settings: Settings, inp: BookingInput, wait_seconds: float) -> BookingOutcome | None:
    try:
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
    except Exception as exc:
        raise TemporalUnavailable(str(exc)) from exc
    workflow_id = booking_workflow_id(inp.idempotency_key)
    try:
        handle = await client.start_workflow(
            BookingWorkflow.run,
            inp,
            id=workflow_id,
            task_queue=settings.temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        # Same idempotency key as an earlier request: attach to that booking, never book twice.
        handle = client.get_workflow_handle(workflow_id, result_type=BookingOutcome)
    try:
        return await asyncio.wait_for(handle.result(), timeout=wait_seconds)
    except TimeoutError:
        return None  # still running durably; the caller is told it is not confirmed yet


def run_booking_workflow(
    settings: Settings, inp: BookingInput, wait_seconds: float
) -> BookingOutcome | None:
    """Blocking. Returns None if the booking is still in progress after `wait_seconds`."""
    return asyncio.run(_run(settings, inp, wait_seconds))


async def _signal_handoff(settings: Settings, escalation_id: uuid.UUID) -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    handle = client.get_workflow_handle(handoff_workflow_id(escalation_id))
    await handle.signal(HandoffWorkflow.status_changed)


def signal_handoff_status_changed(
    settings: Settings, escalation_id: uuid.UUID, timeout: float = 5.0
) -> bool:
    """Best-effort nudge after a dashboard accept/resolve. The database stays authoritative: if
    the signal is lost, the handoff notices the change at its next timer check."""
    try:
        asyncio.run(asyncio.wait_for(_signal_handoff(settings, escalation_id), timeout))
    except Exception as exc:  # workflow finished, Temporal down, timeout - all non-fatal
        logger.warning(
            "handoff_signal_not_delivered",
            extra={"escalation_id": str(escalation_id), "error": type(exc).__name__},
        )
        return False
    return True
