"""Allowed lifecycle transitions. Anything not listed is rejected."""

from app.domain.errors import InvalidTransition
from app.models.enums import EscalationStatus, IntakeStatus

INTAKE_TRANSITIONS: dict[IntakeStatus, frozenset[IntakeStatus]] = {
    IntakeStatus.IN_PROGRESS: frozenset({IntakeStatus.INCOMPLETE, IntakeStatus.PENDING_REVIEW}),
    IntakeStatus.INCOMPLETE: frozenset({IntakeStatus.IN_PROGRESS, IntakeStatus.PENDING_REVIEW}),
    IntakeStatus.PENDING_REVIEW: frozenset({IntakeStatus.CLOSED}),
    IntakeStatus.CLOSED: frozenset(),
}

ESCALATION_TRANSITIONS: dict[EscalationStatus, frozenset[EscalationStatus]] = {
    EscalationStatus.PENDING: frozenset(
        {EscalationStatus.ACCEPTED, EscalationStatus.FAILED, EscalationStatus.RESOLVED}
    ),
    EscalationStatus.ACCEPTED: frozenset({EscalationStatus.RESOLVED}),
    EscalationStatus.FAILED: frozenset({EscalationStatus.RESOLVED}),
    EscalationStatus.RESOLVED: frozenset(),
}


def ensure_intake_transition(current: IntakeStatus, target: IntakeStatus) -> None:
    if target not in INTAKE_TRANSITIONS[current]:
        raise InvalidTransition(
            f"intake cannot move from {current} to {target}",
            current=str(current),
            target=str(target),
        )


def ensure_escalation_transition(current: EscalationStatus, target: EscalationStatus) -> None:
    if target not in ESCALATION_TRANSITIONS[current]:
        raise InvalidTransition(
            f"escalation cannot move from {current} to {target}",
            current=str(current),
            target=str(target),
        )
