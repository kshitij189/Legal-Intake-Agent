import pytest

from app.agent.reply_guard import (
    FALSE_BOOKING,
    UNCONFIRMED_ACCEPTANCE,
    UNSAVED_QUESTION,
    check_reply,
    correction_instruction,
    safe_reply,
)
from app.domain.conversation_state import ConversationState, derive_state
from app.models.enums import FieldStatus, IntakeStatus
from app.schemas.intake import FieldEntry
from tests.helpers import demo_config

CONFIG = demo_config()


def state(
    appointment: str | None = None, escalations: tuple[tuple[str, str], ...] = ()
) -> ConversationState:
    return derive_state(
        CONFIG,
        reference="INT-1",
        status=IntakeStatus.IN_PROGRESS,
        fields={},
        escalations=escalations,
        appointment=appointment,
    )


@pytest.mark.parametrize(
    "reply",
    [
        "Your consultation has been booked for Monday at 10:00.",
        "Great news, your appointment is confirmed with Aarav Mehta.",
        "I've booked a consultation for you tomorrow.",
        "Your appointment\u2019s scheduled for 2 PM.",
        "The consultation is now successfully booked, no need to call back.",
    ],
)
def test_booking_claims_without_a_booking_are_blocked(reply: str) -> None:
    assert check_reply(reply, state()) == [FALSE_BOOKING]


def test_booking_claims_are_fine_when_a_booking_exists() -> None:
    reply = "Your consultation has been booked for Monday at 10:00."
    assert check_reply(reply, state(appointment="2026-10-12T04:30:00+00:00")) == []


@pytest.mark.parametrize(
    "reply",
    [
        "No consultation has been booked yet.",
        "I couldn't book that slot; it is no longer available.",
        "Your consultation hasn't been booked yet.",
        "Would you like me to book a consultation?",
        "Your details have been saved and flagged for the team's prompt review.",
        "Your phone number is confirmed as 98765 43210.",  # not about a booking
    ],
)
def test_negated_or_unrelated_sentences_pass(reply: str) -> None:
    assert check_reply(reply, state()) == []


@pytest.mark.parametrize(
    "reply",
    [
        "A lawyer has already reviewed your case.",
        "The team has accepted your enquiry.",
        "Your case has been accepted by the firm.",
        "Aarav, our employment lawyer, has taken on your matter.",
    ],
)
def test_acceptance_claims_require_an_accepted_escalation(reply: str) -> None:
    assert check_reply(reply, state(escalations=(("imminent_deadline", "PENDING"),))) == [
        UNCONFIRMED_ACCEPTANCE
    ]
    assert check_reply(reply, state(escalations=(("imminent_deadline", "ACCEPTED"),))) == []


def test_honest_handoff_wording_passes() -> None:
    reply = (
        "Your enquiry has been flagged for the team's prompt review. A lawyer has not reviewed it "
        "yet, and nobody has accepted it so far."
    )
    assert check_reply(reply, state(escalations=(("imminent_deadline", "PENDING"),))) == []


def test_both_violations_and_messages() -> None:
    reply = "Your consultation is booked and the lawyer has accepted your case."
    violations = check_reply(reply, state())
    assert violations == [FALSE_BOOKING, UNCONFIRMED_ACCEPTANCE]
    assert "no consultation is booked" in correction_instruction(violations)
    text = safe_reply(violations)
    assert "No consultation has been booked yet." in text
    assert check_reply(text, state()) == []  # the fallback itself never violates


@pytest.mark.parametrize(
    ("reply", "violations"),
    [
        ("Don't worry, the lawyer has accepted your case.", [UNCONFIRMED_ACCEPTANCE]),
        ("No problem - your consultation has been booked.", [FALSE_BOOKING]),
        ("Your consultation has been booked, no need to call back.", [FALSE_BOOKING]),
        ("Sorry, no consultation has been booked yet.", []),
    ],
)
def test_negation_is_scoped_to_the_claims_own_clause(reply: str, violations: list[str]) -> None:
    assert check_reply(reply, state()) == violations


def test_claiming_a_question_was_noted_requires_it_saved() -> None:
    reply = "I can't give legal advice, but I have noted down your question for the lawyers."
    assert check_reply(reply, state()) == [UNSAVED_QUESTION]
    fields = {
        "caller_questions": FieldEntry(value="Can I sue?", status=FieldStatus.REPORTED_BY_CALLER)
    }
    saved = derive_state(CONFIG, reference="INT-1", status=IntakeStatus.IN_PROGRESS, fields=fields)
    assert check_reply(reply, saved) == []
    assert check_reply("I haven't recorded your question yet.", state()) == []
    assert "I haven't recorded your question yet." in safe_reply([UNSAVED_QUESTION])
