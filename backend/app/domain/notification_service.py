"""Reviewer alerts (simulated channel shown in the Phase 7 dashboard). Idempotent per dedupe key:
a retried delivery returns the existing alert instead of creating a second one."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import StaffNotification
from app.models.enums import NotificationKind, StaffRole


def notify(
    session: Session,
    *,
    firm_id: uuid.UUID,
    escalation_id: uuid.UUID,
    recipient_role: StaffRole,
    kind: NotificationKind,
    dedupe_key: str,
    message: str,
) -> tuple[StaffNotification, bool]:
    """Returns (notification, created)."""
    inserted = session.scalar(
        insert(StaffNotification)
        .values(
            id=uuid.uuid4(),
            firm_id=firm_id,
            escalation_id=escalation_id,
            recipient_role=recipient_role,
            kind=kind,
            dedupe_key=dedupe_key,
            message=message,
        )
        .on_conflict_do_nothing(
            index_elements=[StaffNotification.firm_id, StaffNotification.dedupe_key]
        )
        .returning(StaffNotification.id)
    )
    notification = session.scalars(
        select(StaffNotification).where(
            StaffNotification.firm_id == firm_id, StaffNotification.dedupe_key == dedupe_key
        )
    ).one()
    return notification, inserted is not None


def list_notifications(
    session: Session, *, firm_id: uuid.UUID, escalation_id: uuid.UUID | None = None
) -> list[StaffNotification]:
    stmt = select(StaffNotification).where(StaffNotification.firm_id == firm_id)
    if escalation_id is not None:
        stmt = stmt.where(StaffNotification.escalation_id == escalation_id)
    return list(session.scalars(stmt.order_by(StaffNotification.created_at)))
