"""Who is acting, and what they may do. Authorization is enforced here, never by the model."""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import PermissionDenied
from app.models import StaffMember
from app.models.enums import ActorType, StaffRole


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str

    @classmethod
    def agent(cls, agent_id: str = "intake-agent") -> "Actor":
        return cls(ActorType.AGENT, agent_id)

    @classmethod
    def system(cls, component: str = "system") -> "Actor":
        return cls(ActorType.SYSTEM, component)

    @classmethod
    def staff(cls, staff_id: uuid.UUID) -> "Actor":
        return cls(ActorType.STAFF, str(staff_id))


class Action(StrEnum):
    INTAKE_CREATE = "intake.create"
    INTAKE_UPDATE = "intake.update"
    INTAKE_VERIFY_FIELD = "intake.verify_field"
    INTAKE_TRANSITION = "intake.transition"
    INTAKE_CLOSE = "intake.close"
    APPOINTMENT_BOOK = "appointment.book"
    ESCALATION_CREATE = "escalation.create"
    ESCALATION_ACCEPT = "escalation.accept"
    ESCALATION_RESOLVE = "escalation.resolve"
    ESCALATION_MARK_FAILED = "escalation.mark_failed"
    SESSION_MANAGE = "session.manage"


_REVIEWERS = {StaffRole.LAWYER, StaffRole.INTAKE_COORDINATOR}

ALLOWED_ACTOR_TYPES: dict[Action, set[ActorType]] = {
    Action.INTAKE_CREATE: {ActorType.AGENT, ActorType.SYSTEM, ActorType.STAFF},
    Action.INTAKE_UPDATE: {ActorType.AGENT, ActorType.SYSTEM, ActorType.STAFF},
    Action.INTAKE_VERIFY_FIELD: {ActorType.SYSTEM, ActorType.STAFF},
    Action.INTAKE_TRANSITION: {ActorType.AGENT, ActorType.SYSTEM, ActorType.STAFF},
    Action.INTAKE_CLOSE: {ActorType.STAFF},
    Action.APPOINTMENT_BOOK: {ActorType.AGENT, ActorType.SYSTEM, ActorType.STAFF},
    Action.ESCALATION_CREATE: {ActorType.AGENT, ActorType.SYSTEM, ActorType.STAFF},
    Action.ESCALATION_ACCEPT: {ActorType.STAFF},
    Action.ESCALATION_RESOLVE: {ActorType.STAFF},
    Action.ESCALATION_MARK_FAILED: {ActorType.SYSTEM},
    Action.SESSION_MANAGE: {ActorType.AGENT, ActorType.SYSTEM},
}

# Staff actions restricted to specific roles; unlisted actions allow any active staff member.
STAFF_ROLES: dict[Action, set[StaffRole]] = {
    Action.INTAKE_VERIFY_FIELD: _REVIEWERS,
    Action.INTAKE_CLOSE: _REVIEWERS,
    Action.ESCALATION_ACCEPT: _REVIEWERS,
    Action.ESCALATION_RESOLVE: _REVIEWERS,
}


def authorize(
    session: Session, firm_id: uuid.UUID, actor: Actor, action: Action
) -> StaffMember | None:
    """Raise PermissionDenied unless `actor` may perform `action` in this firm.

    Returns the acting staff member for STAFF actors."""
    if actor.type not in ALLOWED_ACTOR_TYPES[action]:
        raise PermissionDenied(f"{actor.type} may not perform {action}", action=str(action))
    if actor.type is not ActorType.STAFF:
        return None

    try:
        staff_id = uuid.UUID(actor.id)
    except ValueError as exc:
        raise PermissionDenied("invalid staff identity", action=str(action)) from exc
    staff = session.scalar(
        select(StaffMember).where(StaffMember.id == staff_id, StaffMember.firm_id == firm_id)
    )
    if staff is None or not staff.is_active:
        raise PermissionDenied("staff member is not active in this firm", action=str(action))
    roles = STAFF_ROLES.get(action)
    if roles is not None and staff.role not in roles:
        raise PermissionDenied(f"role {staff.role} may not perform {action}", action=str(action))
    return staff
