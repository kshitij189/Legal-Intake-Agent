"""Temporal worker + outbox dispatcher (one process: scripts/temporal_worker.py)."""

import asyncio
import contextlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from app.config import Settings
from app.domain import workflow_service
from app.models import WorkflowExecution
from app.models.enums import WorkflowStatus, WorkflowType
from app.workflows.activities import IntakeActivities
from app.workflows.types import HandoffInput, PostCallInput
from app.workflows.workflows import WORKFLOWS, HandoffWorkflow, PostCallWorkflow

logger = logging.getLogger(__name__)

DISPATCH_INTERVAL_SECONDS = 2.0
MAX_DISPATCH_ATTEMPTS = 20


def build_worker(
    client: Client, activities: IntakeActivities, task_queue: str, max_threads: int = 8
) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=WORKFLOWS,
        activities=activities.all(),
        # Activities are synchronous (SQLAlchemy sessions), so they run in threads.
        activity_executor=ThreadPoolExecutor(max_workers=max_threads),
    )


async def start_requested(
    client: Client, execution: WorkflowExecution, task_queue: str
) -> WorkflowHandle[Any, Any]:
    firm_id, entity_id = str(execution.firm_id), str(execution.entity_id)
    if execution.workflow_type is WorkflowType.HANDOFF:
        return await client.start_workflow(
            HandoffWorkflow.run,
            HandoffInput(firm_id, entity_id),
            id=execution.workflow_id,
            task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    if execution.workflow_type is WorkflowType.POST_CALL:
        return await client.start_workflow(
            PostCallWorkflow.run,
            PostCallInput(firm_id, entity_id),
            id=execution.workflow_id,
            task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    raise ValueError(f"{execution.workflow_type} workflows are started by their caller")


def dispatch_pending(
    sessions: sessionmaker[Session],
    client: Client,
    loop: asyncio.AbstractEventLoop,
    task_queue: str,
) -> int:
    """Start REQUESTED workflows (runs in a thread). A start that was already done - e.g. the
    commit below was lost after a crash - is recognised by Temporal and simply marked STARTED."""
    started = 0
    with sessions() as session, session.begin():
        for execution in workflow_service.claim_requested(session):
            future = asyncio.run_coroutine_threadsafe(
                start_requested(client, execution, task_queue), loop
            )
            try:
                future.result(timeout=10)
                started += 1
            except WorkflowAlreadyStartedError:
                pass
            except Exception as exc:
                logger.warning(
                    "workflow_dispatch_failed", extra={"workflow_id": execution.workflow_id}
                )
                workflow_service.mark_dispatch_failed(execution, repr(exc))
                if execution.dispatch_attempts >= MAX_DISPATCH_ATTEMPTS:
                    execution.status = WorkflowStatus.FAILED
                continue
            execution.status = WorkflowStatus.STARTED
    return started


async def run_dispatcher(
    sessions: sessionmaker[Session], client: Client, task_queue: str, stop: asyncio.Event
) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            started = await asyncio.to_thread(dispatch_pending, sessions, client, loop, task_queue)
            if started:
                logger.info("workflows_dispatched count=%d", started)
        except Exception:
            logger.exception("dispatcher_iteration_failed")  # e.g. database briefly unavailable
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=DISPATCH_INTERVAL_SECONDS)


async def run_worker_forever(settings: Settings, sessions: sessionmaker[Session]) -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    activities = IntakeActivities(
        sessions,
        calendar_fault=settings.calendar_fault_injection,
        notifier_fault=settings.notifier_fault_injection,
    )
    stop = asyncio.Event()
    worker = build_worker(client, activities, settings.temporal_task_queue)
    logger.info("temporal_worker_started task_queue=%s", settings.temporal_task_queue)
    await asyncio.gather(
        worker.run(), run_dispatcher(sessions, client, settings.temporal_task_queue, stop)
    )
