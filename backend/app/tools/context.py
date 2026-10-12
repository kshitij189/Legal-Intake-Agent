import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.actors import Actor
from app.domain.errors import DomainError


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ToolContext:
    """Per-conversation state the model cannot set. `intake_id` is reloaded from the call session
    before every tool call, so it always reflects committed state."""

    firm_id: uuid.UUID
    call_session_id: uuid.UUID
    actor: Actor
    intake_id: uuid.UUID | None = None
    offered_slot_ids: set[uuid.UUID] = field(default_factory=set)
    # Book through the durable Temporal workflow (set from settings by the conversation).
    durable_booking: bool = False
    # Temporal task queue for durable booking; None = settings (evaluations use their own queue).
    booking_task_queue: str | None = None
    clock: Callable[[], datetime] = _utc_now


class IntakeNotStarted(DomainError):
    code = "INTAKE_NOT_STARTED"


class SlotNotOffered(DomainError):
    code = "SLOT_NOT_OFFERED"


class VerificationFailed(DomainError):
    code = "VERIFICATION_FAILED"


def require_intake(ctx: ToolContext) -> uuid.UUID:
    if ctx.intake_id is None:
        raise IntakeNotStarted("no draft intake yet - call create_draft_intake first")
    return ctx.intake_id
