"""Run a booking workflow from synchronous code (the in-call `book_consultation` tool)."""

import asyncio
import logging

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import Settings
from app.domain.workflow_service import booking_workflow_id
from app.workflows.types import BookingInput, BookingOutcome
from app.workflows.workflows import BookingWorkflow

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
