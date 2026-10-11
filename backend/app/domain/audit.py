import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domain.actors import Actor
from app.models import AuditEvent
from app.models.enums import AuditOutcome


def record_audit(
    session: Session,
    *,
    firm_id: uuid.UUID,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add an audit event to the caller's transaction. Never put contact details in `details`."""
    event = AuditEvent(
        firm_id=firm_id,
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        outcome=outcome,
        details=details or {},
    )
    session.add(event)
    return event
