"""Transactional outbox for durable work (Temporal workflows).

`request_workflow` is called inside the business transaction that needs the work (e.g. creating
an escalation), so the request commits atomically with it. A dispatcher in the Temporal worker
starts requested workflows using their deterministic `workflow_id`; starting one twice is a no-op.
"""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.clock import resolve_now
from app.domain.errors import NotFound
from app.models import WorkflowExecution
from app.models.enums import WorkflowStatus, WorkflowType

MAX_ERROR_LENGTH = 1000


def handoff_workflow_id(escalation_id: uuid.UUID) -> str:
    return f"handoff-{escalation_id}"


def post_call_workflow_id(call_session_id: uuid.UUID) -> str:
    return f"post-call-{call_session_id}"


def booking_workflow_id(idempotency_key: str) -> str:
    return f"booking-{idempotency_key}"


def request_workflow(
    session: Session,
    *,
    firm_id: uuid.UUID,
    workflow_id: str,
    workflow_type: WorkflowType,
    entity_type: str,
    entity_id: uuid.UUID,
    status: WorkflowStatus = WorkflowStatus.REQUESTED,
) -> None:
    """Idempotent: a second request for the same workflow id changes nothing."""
    session.execute(
        insert(WorkflowExecution)
        .values(
            id=uuid.uuid4(),
            firm_id=firm_id,
            workflow_id=workflow_id,
            workflow_type=workflow_type,
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            dispatch_attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[WorkflowExecution.workflow_id])
    )


def claim_requested(
    session: Session, *, limit: int = 20, firm_ids: Collection[uuid.UUID] | None = None
) -> list[WorkflowExecution]:
    """System dispatcher only (all tenants, unless `firm_ids` narrows it, e.g. for
    evaluations). Rows stay locked until the caller's commit, and concurrent dispatchers
    skip them."""
    stmt = select(WorkflowExecution).where(WorkflowExecution.status == WorkflowStatus.REQUESTED)
    if firm_ids is not None:
        stmt = stmt.where(WorkflowExecution.firm_id.in_(list(firm_ids)))
    return list(
        session.scalars(
            stmt.order_by(WorkflowExecution.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )


def get_execution(session: Session, *, workflow_id: str) -> WorkflowExecution:
    execution = session.scalar(
        select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id)
    )
    if execution is None:
        raise NotFound("workflow execution not found", workflow_id=workflow_id)
    return execution


def mark_dispatch_failed(execution: WorkflowExecution, error: str) -> None:
    execution.dispatch_attempts += 1
    execution.last_error = error[:MAX_ERROR_LENGTH]


def mark_started(
    session: Session, *, workflow_id: str, now: datetime | None = None
) -> WorkflowExecution:
    execution = get_execution(session, workflow_id=workflow_id)
    if execution.status in (WorkflowStatus.REQUESTED, WorkflowStatus.STARTED):
        execution.status = WorkflowStatus.STARTED
        execution.started_at = execution.started_at or resolve_now(now)
    session.flush()
    return execution


def mark_finished(
    session: Session,
    *,
    workflow_id: str,
    succeeded: bool,
    result: str | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> WorkflowExecution:
    execution = get_execution(session, workflow_id=workflow_id)
    execution.status = WorkflowStatus.COMPLETED if succeeded else WorkflowStatus.FAILED
    execution.result = result
    if error:
        execution.last_error = error[:MAX_ERROR_LENGTH]
    execution.completed_at = resolve_now(now)
    session.flush()
    return execution
