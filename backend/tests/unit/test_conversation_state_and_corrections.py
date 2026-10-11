from datetime import timedelta

from app.domain import policy_engine
from app.domain.conversation_state import (
    CREATE_DRAFT_INTAKE,
    FINALIZE_WHEN_DONE,
    INTAKE_SUBMITTED,
    derive_state,
)
from app.models.enums import FieldStatus, IntakeStatus
from app.schemas.intake import MAX_FIELD_HISTORY, FieldEntry
from tests.helpers import NOW, demo_config

CONFIG = demo_config()
REQUIRED = ["caller_name", "phone", "preferred_contact_method", "matter_category", "matter_summary"]


def filled(**overrides: FieldEntry) -> dict[str, FieldEntry]:
    fields = policy_engine.initial_fields(CONFIG)
    fields.update(overrides)
    return fields


def test_next_action_follows_the_questionnaire() -> None:
    assert derive_state(CONFIG, reference=None, status=None, fields={}).next_action == (
        CREATE_DRAFT_INTAKE
    )
    empty = derive_state(
        CONFIG, reference="INT-1", status=IntakeStatus.IN_PROGRESS, fields=filled()
    )
    assert empty.next_action == "COLLECT_CALLER_NAME"
    assert list(empty.missing_required) == REQUIRED

    named = filled(
        caller_name=FieldEntry(value="Priya", status=FieldStatus.REPORTED_BY_CALLER),
        phone=FieldEntry(status=FieldStatus.DECLINED_BY_CALLER),
    )
    state = derive_state(CONFIG, reference="INT-1", status=IntakeStatus.IN_PROGRESS, fields=named)
    assert state.next_action == "COLLECT_PREFERRED_CONTACT_METHOD"  # a refusal is not re-asked
    assert state.declined == ("phone",)


def test_complete_and_submitted_states() -> None:
    complete = filled(
        **{k: FieldEntry(value="x", status=FieldStatus.REPORTED_BY_CALLER) for k in REQUIRED}
    )
    state = derive_state(
        CONFIG, reference="INT-1", status=IntakeStatus.IN_PROGRESS, fields=complete
    )
    assert state.next_action == FINALIZE_WHEN_DONE
    submitted = derive_state(
        CONFIG, reference="INT-1", status=IntakeStatus.PENDING_REVIEW, fields=complete
    )
    assert submitted.next_action == INTAKE_SUBMITTED


def test_prompt_text_reflects_stored_state() -> None:
    fields = filled(
        caller_name=FieldEntry(value="Priya", status=FieldStatus.REPORTED_BY_CALLER),
        reported_deadline=FieldEntry(status=FieldStatus.PENDING_HUMAN_REVIEW, note="end of month"),
    )
    text = derive_state(
        CONFIG,
        reference="INT-7",
        status=IntakeStatus.IN_PROGRESS,
        fields=fields,
        escalations=(("imminent_deadline", "PENDING"),),
    ).to_prompt()
    assert "intake INT-7, status IN_PROGRESS" in text
    assert "caller_name='Priya' [REPORTED_BY_CALLER]" in text
    assert "pending human review: reported_deadline" not in text  # optional field, not required
    assert "imminent_deadline (PENDING)" in text
    assert "booked consultation: none" in text
    assert "next action: COLLECT_PHONE" in text


def entry(value: str | None, status: FieldStatus, minutes: int = 0) -> FieldEntry:
    return FieldEntry(value=value, status=status, updated_at=NOW + timedelta(minutes=minutes))


def test_corrections_keep_bounded_history() -> None:
    current = FieldEntry()
    for i in range(MAX_FIELD_HISTORY + 3):
        current = policy_engine.merge_with_previous(
            current, entry(f"98765432{i:02d}", FieldStatus.REPORTED_BY_CALLER, i)
        )
    assert current.value == f"98765432{MAX_FIELD_HISTORY + 2:02d}"
    assert len(current.history) == MAX_FIELD_HISTORY
    assert current.history[0].value == f"98765432{MAX_FIELD_HISTORY + 1:02d}"  # most recent first


def test_unchanged_resave_and_first_answer_add_no_history() -> None:
    first = policy_engine.merge_with_previous(
        FieldEntry(), entry("Priya", FieldStatus.REPORTED_BY_CALLER)
    )
    assert first.history == ()
    same = policy_engine.merge_with_previous(first, entry("Priya", FieldStatus.REPORTED_BY_CALLER))
    assert same.history == ()
    confirmed = policy_engine.merge_with_previous(
        first, entry("Priya", FieldStatus.CONFIRMED_BY_CALLER)
    )
    assert [p.status for p in confirmed.history] == [FieldStatus.REPORTED_BY_CALLER]


def test_summary_facts_must_be_in_the_record() -> None:
    fields = filled(
        phone=FieldEntry(value="9876543210", status=FieldStatus.REPORTED_BY_CALLER),
        email=FieldEntry(value="priya@example.com", status=FieldStatus.REPORTED_BY_CALLER),
        reported_deadline=FieldEntry(value="2026-10-13", status=FieldStatus.REPORTED_BY_CALLER),
    )
    ok = (
        "Caller (98765 43210, Priya@Example.com) reports a notice due 2026-10-13 "
        "after two months of unpaid salary of 45000 rupees."
    )
    assert policy_engine.unsupported_summary_facts(ok, fields) == []
    bad = "Deadline 2026-10-20; call +91 99999 11111 or write to boss@corp.example."
    assert policy_engine.unsupported_summary_facts(bad, fields) == [
        "date 2026-10-20",
        "email boss@corp.example",
        "phone +91 99999 11111",
    ]


def test_summary_accepts_stt_formatted_stored_phone() -> None:
    fields = filled(phone=FieldEntry(value="9876543210", status=FieldStatus.REPORTED_BY_CALLER))
    assert policy_engine.unsupported_summary_facts("Call 98,765 43,210.", fields) == []
    assert policy_engine.unsupported_summary_facts("Call 91,234 56,789.", fields) == [
        "phone 91,234 56,789"
    ]
