import pytest

from app.agent.omission_check import find_omissions, nudge
from app.domain import policy_engine
from app.domain.conversation_state import ConversationState, derive_state
from app.models.enums import FieldStatus, IntakeStatus
from app.schemas.intake import FieldEntry
from tests.helpers import demo_config

CONFIG = demo_config()


def state(reference: str | None = "INT-1", **saved: FieldStatus) -> ConversationState:
    fields = policy_engine.initial_fields(CONFIG)
    for key, status in saved.items():
        value = None if status in (FieldStatus.DECLINED_BY_CALLER, FieldStatus.MISSING) else "x"
        fields[key] = FieldEntry(value=value, status=status, note="n")
    return derive_state(
        CONFIG,
        reference=reference,
        status=IntakeStatus.IN_PROGRESS if reference else None,
        fields=fields,
    )


def codes(text: str, s: ConversationState | None = None) -> list[str]:
    return [o.code for o in find_omissions(text, s or state())]


@pytest.mark.parametrize(
    "text",
    [
        "I received a notice saying I must respond by tomorrow.",
        "The hearing is sometime in November.",
        "It says the 20th of November 2026.",
        "They gave me until next Friday.",
        "I have to reply within 7 days.",
        "The court date is 2026-11-20.",
        "It's due at the end of the month.",
    ],
)
def test_deadline_cues_are_detected(text: str) -> None:
    assert codes(text) == ["possible_deadline"]


@pytest.mark.parametrize(
    "text",
    [
        "There's no deadline or notice involved.",
        "My landlord kept my deposit.",
        "I noticed the salary was missing.",
    ],
)
def test_non_deadline_text_is_ignored(text: str) -> None:
    assert codes(text) == []


def test_saved_fields_are_not_flagged_again() -> None:
    text = "Respond by tomorrow, call me on 98765 43210 or priya@example.com."
    assert codes(text) == ["possible_deadline", "possible_phone", "possible_email"]
    saved = state(
        reported_deadline=FieldStatus.PENDING_HUMAN_REVIEW,
        phone=FieldStatus.REPORTED_BY_CALLER,
        email=FieldStatus.REPORTED_BY_CALLER,
    )
    assert codes(text, saved) == []


def test_refusal_hint_names_the_refused_fields() -> None:
    text = "No, I don't want to share my number or email either."
    [omission] = find_omissions(text, state())
    assert omission.code == "possible_refusal"
    assert "for: phone, email." in omission.hint
    [partial] = find_omissions(text, state(phone=FieldStatus.DECLINED_BY_CALLER))
    assert "for: email." in partial.hint
    both = state(phone=FieldStatus.DECLINED_BY_CALLER, email=FieldStatus.DECLINED_BY_CALLER)
    assert codes(text, both) == []


def test_unspecific_refusal_only_fires_when_nothing_is_recorded() -> None:
    text = "I'd rather not say."
    assert codes(text) == ["possible_refusal"]
    assert codes(text, state(caller_name=FieldStatus.DECLINED_BY_CALLER)) == []


@pytest.mark.parametrize("text", ["That's all, bye.", "No, that's everything, thanks.", "Goodbye!"])
def test_caller_finishing_an_unfinalized_intake_is_flagged(text: str) -> None:
    assert codes(text) == ["possible_finish"]


def test_finish_is_not_flagged_once_submitted() -> None:
    submitted = derive_state(
        CONFIG,
        reference="INT-1",
        status=IntakeStatus.PENDING_REVIEW,
        fields=policy_engine.initial_fields(CONFIG),
    )
    assert codes("That's all, bye.", submitted) == []


def test_nothing_is_flagged_before_an_intake_exists() -> None:
    assert codes("Respond by tomorrow.", state(reference=None)) == []


def test_nudge_lists_every_hint() -> None:
    text = nudge(find_omissions("Due tomorrow. Call 98765 43210.", state()))
    assert text.startswith("[Automated check]")
    assert "reported_deadline" in text and "phone" in text


@pytest.mark.parametrize(
    "text",
    [
        "Can I sue them? What are my chances of winning?",
        "Should I just refuse to leave?",
        "Do I have a case against my employer?",
    ],
)
def test_legal_questions_must_be_recorded(text: str) -> None:
    assert "possible_legal_question" in codes(text)
    assert "possible_legal_question" not in codes(
        text, state(caller_questions=FieldStatus.REPORTED_BY_CALLER)
    )


def test_speech_to_text_number_format_is_detected() -> None:
    assert codes("My number is 98,765 43,210.") == ["possible_phone"]
