"""Firm-configured intake policy: questionnaire, escalation rules and scheduling limits.

Stored as JSONB on `firms.config` and validated here. Escalation criteria come only from this
configuration - the model never decides what is urgent.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import Priority

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
CODE_PATTERN = r"^[A-Z][A-Z0-9_]*$"
MATTER_CATEGORY_KEY = "matter_category"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QuestionType(StrEnum):
    TEXT = "TEXT"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    DATE = "DATE"
    CHOICE = "CHOICE"


class ChoiceOption(StrictModel):
    code: str = Field(pattern=CODE_PATTERN, max_length=64)
    label: str = Field(min_length=1, max_length=200)


class IntakeQuestion(StrictModel):
    key: str = Field(pattern=KEY_PATTERN, max_length=64)
    prompt: str = Field(min_length=1, max_length=500)
    type: QuestionType
    required: bool = True
    choices: tuple[ChoiceOption, ...] = ()

    @model_validator(mode="after")
    def _check_choices(self) -> Self:
        if self.type is QuestionType.CHOICE:
            codes = [c.code for c in self.choices]
            if not codes:
                raise ValueError(f"choice question '{self.key}' needs at least one choice")
            if len(set(codes)) != len(codes):
                raise ValueError(f"choice question '{self.key}' has duplicate codes")
        elif self.choices:
            raise ValueError(f"only CHOICE questions may define choices ('{self.key}')")
        return self

    @property
    def choice_codes(self) -> frozenset[str]:
        return frozenset(c.code for c in self.choices)


class DeadlineWithinDays(StrictModel):
    """Matches when a date field holds a date at most `days` days after the firm-local today
    (dates in the past also match)."""

    kind: Literal["deadline_within_days"]
    field_key: str
    days: int = Field(ge=0, le=365)


class FieldValueIn(StrictModel):
    """Matches when a choice field holds one of the listed codes."""

    kind: Literal["field_value_in"]
    field_key: str
    values: tuple[str, ...] = Field(min_length=1)


RuleCondition = Annotated[DeadlineWithinDays | FieldValueIn, Field(discriminator="kind")]


class EscalationRule(StrictModel):
    id: str = Field(pattern=KEY_PATTERN, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    priority: Priority
    condition: RuleCondition


class SchedulingPolicy(StrictModel):
    consultation_minutes: int = Field(ge=15, le=240)
    min_notice_hours: int = Field(ge=0, le=168)
    max_days_ahead: int = Field(ge=1, le=90)


class FirmConfig(StrictModel):
    intake_questions: tuple[IntakeQuestion, ...] = Field(min_length=1)
    escalation_rules: tuple[EscalationRule, ...] = ()
    scheduling: SchedulingPolicy

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        keys = [q.key for q in self.intake_questions]
        if len(set(keys)) != len(keys):
            raise ValueError("intake question keys must be unique")
        category = self.question(MATTER_CATEGORY_KEY)
        if category is None or category.type is not QuestionType.CHOICE:
            raise ValueError(f"a CHOICE question '{MATTER_CATEGORY_KEY}' is required")

        rule_ids = [r.id for r in self.escalation_rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("escalation rule ids must be unique")
        for rule in self.escalation_rules:
            cond = rule.condition
            question = self.question(cond.field_key)
            if question is None:
                raise ValueError(f"rule '{rule.id}' references unknown field '{cond.field_key}'")
            if isinstance(cond, DeadlineWithinDays) and question.type is not QuestionType.DATE:
                raise ValueError(f"rule '{rule.id}' needs a DATE field")
            if isinstance(cond, FieldValueIn):
                if question.type is not QuestionType.CHOICE:
                    raise ValueError(f"rule '{rule.id}' needs a CHOICE field")
                unknown = set(cond.values) - question.choice_codes
                if unknown:
                    raise ValueError(f"rule '{rule.id}' uses unknown codes {sorted(unknown)}")
        return self

    def question(self, key: str) -> IntakeQuestion | None:
        return next((q for q in self.intake_questions if q.key == key), None)

    def rule(self, rule_id: str) -> EscalationRule | None:
        return next((r for r in self.escalation_rules if r.id == rule_id), None)

    @property
    def matter_categories(self) -> tuple[ChoiceOption, ...]:
        question = self.question(MATTER_CATEGORY_KEY)
        assert question is not None  # guaranteed by validation
        return question.choices
