"""Deterministic intake policy: answer validation, escalation rules and completeness.

Pure functions - no database access and no implicit clock. Callers pass `today`/`now`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.domain.errors import ValidationFailed
from app.models.enums import ActorType, FieldStatus, Priority
from app.schemas.firm_config import (
    DeadlineWithinDays,
    EscalationRule,
    FieldValueIn,
    FirmConfig,
    IntakeQuestion,
    QuestionType,
)
from app.schemas.intake import MAX_FIELD_HISTORY, FieldEntry, PriorValue

MAX_TEXT_LENGTH = 2000
MAX_NOTE_LENGTH = 1000

# Commas included: speech-to-text writes spoken numbers as "98,765 43,210".
_PHONE_SEPARATORS = re.compile(r"[\s\-().,]")
_PHONE = re.compile(r"^\+?\d{7,15}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_value(question: IntakeQuestion, raw: str) -> str:
    """Validate `raw` against the question type and return its canonical form."""
    value = raw.strip()
    if not value:
        raise ValidationFailed("value must not be empty", field_key=question.key)

    match question.type:
        case QuestionType.TEXT:
            if len(value) > MAX_TEXT_LENGTH:
                raise ValidationFailed("text is too long", field_key=question.key)
            return value
        case QuestionType.PHONE:
            phone = _PHONE_SEPARATORS.sub("", value)
            if not _PHONE.match(phone):
                raise ValidationFailed("not a valid phone number", field_key=question.key)
            digits = len(phone.lstrip("+"))
            if question.phone_digit_counts and digits not in question.phone_digit_counts:
                raise ValidationFailed(
                    f"the number has {digits} digits; numbers here have"
                    f" {' or '.join(map(str, question.phone_digit_counts))} digits. Do not save"
                    " it: read the digits back and ask the caller to repeat the number",
                    field_key=question.key,
                )
            return phone
        case QuestionType.EMAIL:
            if len(value) > 320 or not _EMAIL.match(value):
                raise ValidationFailed("not a valid email address", field_key=question.key)
            return value.lower()
        case QuestionType.DATE:
            try:
                if not _ISO_DATE.match(value):
                    raise ValueError(value)
                return date.fromisoformat(value).isoformat()
            except ValueError as exc:
                raise ValidationFailed(
                    "date must be an exact calendar date (YYYY-MM-DD); record ambiguous dates"
                    " as PENDING_HUMAN_REVIEW with a note",
                    field_key=question.key,
                ) from exc
        case QuestionType.CHOICE:
            code = value.upper()
            if code not in question.choice_codes:
                raise ValidationFailed(
                    "value is not one of the configured choices",
                    field_key=question.key,
                    allowed=sorted(question.choice_codes),
                )
            return code


def build_field_entry(
    question: IntakeQuestion,
    *,
    status: FieldStatus,
    value: str | None,
    note: str | None,
    source: ActorType,
    now: datetime,
) -> FieldEntry:
    """Apply the provenance rules: refusals and gaps are recorded, never filled in."""
    if status is FieldStatus.MISSING:
        raise ValidationFailed("MISSING is the initial state and cannot be saved")

    clean_note = note.strip() if note else None
    if clean_note and len(clean_note) > MAX_NOTE_LENGTH:
        raise ValidationFailed("note is too long", field_key=question.key)

    if status is FieldStatus.DECLINED_BY_CALLER:
        if value is not None:
            raise ValidationFailed("a declined field cannot carry a value", field_key=question.key)
        normalized = None
    elif status is FieldStatus.PENDING_HUMAN_REVIEW:
        if not clean_note:
            raise ValidationFailed(
                "PENDING_HUMAN_REVIEW needs a note describing the ambiguity",
                field_key=question.key,
            )
        normalized = normalize_value(question, value) if value is not None else None
    else:
        if value is None:
            raise ValidationFailed(f"{status} requires a value", field_key=question.key)
        normalized = normalize_value(question, value)

    return FieldEntry(
        value=normalized, status=status, note=clean_note, source=source, updated_at=now
    )


def merge_with_previous(previous: FieldEntry, new: FieldEntry) -> FieldEntry:
    """Carry correction history forward: a changed answer keeps what it replaced (bounded)."""
    changed = (previous.value, previous.status) != (new.value, new.status)
    if previous.status is FieldStatus.MISSING or not changed:
        return new.model_copy(update={"history": previous.history})
    prior = PriorValue(value=previous.value, status=previous.status, updated_at=previous.updated_at)
    return new.model_copy(update={"history": (prior, *previous.history)[:MAX_FIELD_HISTORY]})


def initial_fields(config: FirmConfig) -> dict[str, FieldEntry]:
    return {q.key: FieldEntry() for q in config.intake_questions}


def parse_fields(raw: Mapping[str, Any]) -> dict[str, FieldEntry]:
    return {key: FieldEntry.model_validate(entry) for key, entry in raw.items()}


def dump_fields(fields: Mapping[str, FieldEntry]) -> dict[str, Any]:
    return {key: entry.model_dump(mode="json") for key, entry in fields.items()}


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    priority: Priority
    description: str
    field_key: str
    field_value: str
    field_status: FieldStatus


def evaluate_rule(
    rule: EscalationRule, fields: Mapping[str, FieldEntry], today: date
) -> RuleMatch | None:
    cond = rule.condition
    entry = fields.get(cond.field_key)
    # Any stored value counts, including unverified and pending-review ones: for escalation,
    # an uncertain reported deadline must still reach a human.
    if entry is None or entry.value is None:
        return None

    matched = False
    if isinstance(cond, DeadlineWithinDays):
        matched = (date.fromisoformat(entry.value) - today).days <= cond.days
    elif isinstance(cond, FieldValueIn):
        matched = entry.value in cond.values

    if not matched:
        return None
    return RuleMatch(
        rule_id=rule.id,
        priority=rule.priority,
        description=rule.description,
        field_key=cond.field_key,
        field_value=entry.value,
        field_status=entry.status,
    )


def evaluate_rules(
    config: FirmConfig, fields: Mapping[str, FieldEntry], today: date
) -> list[RuleMatch]:
    matches = (evaluate_rule(rule, fields, today) for rule in config.escalation_rules)
    return [m for m in matches if m is not None]


_SUMMARY_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SUMMARY_EMAIL = re.compile(r"[^@\s(),;:]+@[^@\s(),;:]+\.[A-Za-z]{2,}")
_SUMMARY_PHONE = re.compile(r"\+?\d[\d\s\-().,]{5,}\d")


def _digits(text: str) -> str:
    return ("+" if text.strip().startswith("+") else "") + re.sub(r"\D", "", text)


def unsupported_summary_facts(summary: str, fields: Mapping[str, FieldEntry]) -> list[str]:
    """Exact dates, emails and phone numbers in a summary must match stored field values, so a
    summary cannot introduce contact details or deadlines the intake record does not contain."""
    stored = [e.value for e in fields.values() if e.value]
    stored_lower = {v.lower() for v in stored}
    stored_digits = {_digits(v) for v in stored} | {_digits(v).lstrip("+") for v in stored}
    problems: list[str] = []

    remaining = summary
    for match in _SUMMARY_DATE.findall(summary):
        if match not in stored:
            problems.append(f"date {match}")
        remaining = remaining.replace(match, " ")
    for match in _SUMMARY_EMAIL.findall(remaining):
        if match.lower().rstrip(".") not in stored_lower:
            problems.append(f"email {match}")
        remaining = remaining.replace(match, " ")
    for match in _SUMMARY_PHONE.findall(remaining):
        digits = _digits(match)
        if len(digits.lstrip("+")) >= 7 and digits not in stored_digits:
            problems.append(f"phone {match.strip()}")
    return problems


@dataclass(frozen=True)
class Completeness:
    missing: tuple[str, ...]
    declined: tuple[str, ...]
    needs_review: tuple[str, ...]

    @property
    def all_required_answered(self) -> bool:
        return not self.missing


def completeness(config: FirmConfig, fields: Mapping[str, FieldEntry]) -> Completeness:
    """Status of the required questions (optional questions never block an intake)."""
    missing: list[str] = []
    declined: list[str] = []
    review: list[str] = []
    for question in config.intake_questions:
        if not question.required:
            continue
        status = fields.get(question.key, FieldEntry()).status
        if status is FieldStatus.MISSING:
            missing.append(question.key)
        elif status is FieldStatus.DECLINED_BY_CALLER:
            declined.append(question.key)
        elif status is FieldStatus.PENDING_HUMAN_REVIEW:
            review.append(question.key)
    return Completeness(tuple(missing), tuple(declined), tuple(review))
