import pytest

from app.domain.errors import InvalidTransition
from app.domain.transitions import (
    ESCALATION_TRANSITIONS,
    INTAKE_TRANSITIONS,
    ensure_escalation_transition,
    ensure_intake_transition,
)
from app.models.enums import EscalationStatus, IntakeStatus


@pytest.mark.parametrize("current", list(IntakeStatus))
@pytest.mark.parametrize("target", list(IntakeStatus))
def test_intake_transitions(current: IntakeStatus, target: IntakeStatus) -> None:
    if target in INTAKE_TRANSITIONS[current]:
        ensure_intake_transition(current, target)
    else:
        with pytest.raises(InvalidTransition):
            ensure_intake_transition(current, target)


@pytest.mark.parametrize("current", list(EscalationStatus))
@pytest.mark.parametrize("target", list(EscalationStatus))
def test_escalation_transitions(current: EscalationStatus, target: EscalationStatus) -> None:
    if target in ESCALATION_TRANSITIONS[current]:
        ensure_escalation_transition(current, target)
    else:
        with pytest.raises(InvalidTransition):
            ensure_escalation_transition(current, target)


def test_terminal_and_key_states() -> None:
    assert INTAKE_TRANSITIONS[IntakeStatus.CLOSED] == frozenset()
    assert ESCALATION_TRANSITIONS[EscalationStatus.RESOLVED] == frozenset()
    # A failed handoff is never silently turned into an accepted one.
    assert EscalationStatus.ACCEPTED not in ESCALATION_TRANSITIONS[EscalationStatus.FAILED]
