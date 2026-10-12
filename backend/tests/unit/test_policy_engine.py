from datetime import timedelta

import pytest

from app.domain import policy_engine
from app.domain.errors import ValidationFailed
from app.models.enums import ActorType, FieldStatus, Priority
from app.schemas.firm_config import IntakeQuestion
from app.schemas.intake import FieldEntry
from tests.helpers import FIRM_TODAY, NOW, demo_config

CONFIG = demo_config()


def q(key: str) -> IntakeQuestion:
    question = CONFIG.question(key)
    assert question is not None
    return question


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("phone", " +91 98765-43210 ", "+919876543210"),
        ("phone", "(020) 5555.1234", "02055551234"),
        ("phone", "98,765 43,210", "9876543210"),  # how speech-to-text writes spoken numbers
        ("email", "Priya@Example.COM", "priya@example.com"),
        ("reported_deadline", "2026-10-13", "2026-10-13"),
        ("matter_category", " employment ", "EMPLOYMENT"),
        ("matter_summary", "  Salary withheld.  ", "Salary withheld."),
    ],
)
def test_normalize_value_accepts_and_canonicalizes(key: str, raw: str, expected: str) -> None:
    assert policy_engine.normalize_value(q(key), raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("phone", "12345"),
        ("phone", "call me maybe"),
        ("email", "not-an-email"),
        ("reported_deadline", "tomorrow"),
        ("reported_deadline", "13/10/2026"),
        ("reported_deadline", "2026-02-30"),
        ("matter_category", "CRIMINAL"),
        ("caller_name", "   "),
        ("matter_summary", "x" * (policy_engine.MAX_TEXT_LENGTH + 1)),
    ],
)
def test_normalize_value_rejects_invalid(key: str, raw: str) -> None:
    with pytest.raises(ValidationFailed):
        policy_engine.normalize_value(q(key), raw)


@pytest.mark.parametrize(
    "heard",
    [
        "94,475,667",  # Phase 6 audio: "94470 55667" with digits dropped (8 digits)
        "98,111,0099",  # "98111 00999" with a digit dropped (9 digits)
        "900007060",  # "90080 70060" misheard (9 digits)
        "+1 555 0100 2",  # 9 digits
    ],
)
def test_phone_with_implausible_digit_count_is_rejected(heard: str) -> None:
    """Speech-to-text that drops or merges digits must not be saved as the callback number."""
    with pytest.raises(ValidationFailed, match="repeat the number"):
        policy_engine.normalize_value(q("phone"), heard)


@pytest.mark.parametrize("raw", ["98765 43210", "+91 99887 76655", "011 2345 6789"])
def test_phone_with_configured_digit_count_is_accepted(raw: str) -> None:
    policy_engine.normalize_value(q("phone"), raw)


def test_phone_digit_counts_only_for_phone_questions() -> None:
    with pytest.raises(ValueError, match="only PHONE"):
        IntakeQuestion(key="x", prompt="x", type="TEXT", phone_digit_counts=(10,))
    with pytest.raises(ValueError, match="between 7 and 15"):
        IntakeQuestion(key="x", prompt="x", type="PHONE", phone_digit_counts=(3,))
    unrestricted = IntakeQuestion(key="x", prompt="x", type="PHONE")
    assert policy_engine.normalize_value(unrestricted, "5551234") == "5551234"


def build(
    key: str, status: FieldStatus, value: str | None = None, note: str | None = None
) -> FieldEntry:
    return policy_engine.build_field_entry(
        q(key), status=status, value=value, note=note, source=ActorType.AGENT, now=NOW
    )


def test_missing_cannot_be_saved() -> None:
    with pytest.raises(ValidationFailed, match="initial state"):
        build("phone", FieldStatus.MISSING)


def test_declined_records_refusal_without_value() -> None:
    entry = build("phone", FieldStatus.DECLINED_BY_CALLER, note="Caller prefers not to say")
    assert entry.value is None
    assert entry.status is FieldStatus.DECLINED_BY_CALLER
    with pytest.raises(ValidationFailed, match="declined"):
        build("phone", FieldStatus.DECLINED_BY_CALLER, value="9876543210")


def test_reported_and_confirmed_require_a_valid_value() -> None:
    for status in (FieldStatus.REPORTED_BY_CALLER, FieldStatus.CONFIRMED_BY_CALLER):
        with pytest.raises(ValidationFailed, match="requires a value"):
            build("caller_name", status)
    entry = build("caller_name", FieldStatus.CONFIRMED_BY_CALLER, value="Priya Sharma")
    assert (entry.value, entry.source, entry.updated_at) == ("Priya Sharma", ActorType.AGENT, NOW)


def test_pending_review_preserves_ambiguity_with_a_note() -> None:
    with pytest.raises(ValidationFailed, match="note"):
        build("reported_deadline", FieldStatus.PENDING_HUMAN_REVIEW)
    entry = build(
        "reported_deadline",
        FieldStatus.PENDING_HUMAN_REVIEW,
        note="Caller said 'end of next week', exact date unknown",
    )
    assert entry.value is None


def test_note_length_is_bounded() -> None:
    with pytest.raises(ValidationFailed, match="note"):
        build("caller_name", FieldStatus.REPORTED_BY_CALLER, value="A", note="x" * 1001)


def fields_with(key: str, value: str | None, status: FieldStatus) -> dict[str, FieldEntry]:
    fields = policy_engine.initial_fields(CONFIG)
    fields[key] = FieldEntry(value=value, status=status)
    return fields


@pytest.mark.parametrize(
    ("days_from_today", "should_match"),
    [(-1, True), (0, True), (1, True), (3, True), (4, False), (30, False)],
)
def test_deadline_rule_window(days_from_today: int, should_match: bool) -> None:
    deadline = (FIRM_TODAY + timedelta(days=days_from_today)).isoformat()
    fields = fields_with("reported_deadline", deadline, FieldStatus.REPORTED_BY_CALLER)
    matches = policy_engine.evaluate_rules(CONFIG, fields, FIRM_TODAY)
    assert [m.rule_id for m in matches] == (["imminent_deadline"] if should_match else [])
    if should_match:
        assert matches[0].priority is Priority.URGENT
        assert matches[0].field_status is FieldStatus.REPORTED_BY_CALLER


def test_unverified_pending_review_deadline_still_escalates() -> None:
    fields = fields_with("reported_deadline", "2026-10-13", FieldStatus.PENDING_HUMAN_REVIEW)
    assert [m.rule_id for m in policy_engine.evaluate_rules(CONFIG, fields, FIRM_TODAY)] == [
        "imminent_deadline"
    ]


@pytest.mark.parametrize("status", [FieldStatus.MISSING, FieldStatus.DECLINED_BY_CALLER])
def test_absent_values_never_trigger_rules(status: FieldStatus) -> None:
    fields = fields_with("reported_deadline", None, status)
    assert policy_engine.evaluate_rules(CONFIG, fields, FIRM_TODAY) == []


def test_value_rule_for_out_of_scope_category() -> None:
    other = fields_with("matter_category", "OTHER", FieldStatus.REPORTED_BY_CALLER)
    employment = fields_with("matter_category", "EMPLOYMENT", FieldStatus.REPORTED_BY_CALLER)
    assert [m.rule_id for m in policy_engine.evaluate_rules(CONFIG, other, FIRM_TODAY)] == [
        "outside_intake_workflow"
    ]
    assert policy_engine.evaluate_rules(CONFIG, employment, FIRM_TODAY) == []


def test_completeness_tracks_required_fields_only() -> None:
    fields = policy_engine.initial_fields(CONFIG)
    fields["caller_name"] = FieldEntry(value="Priya", status=FieldStatus.CONFIRMED_BY_CALLER)
    fields["phone"] = FieldEntry(status=FieldStatus.DECLINED_BY_CALLER)
    fields["matter_summary"] = FieldEntry(status=FieldStatus.PENDING_HUMAN_REVIEW, note="unclear")
    result = policy_engine.completeness(CONFIG, fields)
    assert result.missing == ("preferred_contact_method", "matter_category")
    assert result.declined == ("phone",)
    assert result.needs_review == ("matter_summary",)
    assert not result.all_required_answered


def test_fields_roundtrip_through_json() -> None:
    fields = fields_with("reported_deadline", "2026-10-13", FieldStatus.REPORTED_BY_CALLER)
    assert policy_engine.parse_fields(policy_engine.dump_fields(fields)) == fields
