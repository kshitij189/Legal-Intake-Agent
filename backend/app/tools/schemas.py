"""Strict tool inputs (what the model may send) and the structured result envelope.

`firm_id`, `intake_id` and the actor never appear here: they come from the session context, so
the model cannot address another firm's or another caller's records.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# The agent can record what the caller said; it can never mark anything verified.
AgentFieldStatus = Literal[
    "REPORTED_BY_CALLER", "CONFIRMED_BY_CALLER", "DECLINED_BY_CALLER", "PENDING_HUMAN_REVIEW"
]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArguments(ToolInput):
    pass


class SaveIntakeAnswerInput(ToolInput):
    field_key: str = Field(max_length=64, description="Key of the intake question being answered.")
    status: AgentFieldStatus = Field(
        description=(
            "REPORTED_BY_CALLER: stated by the caller. CONFIRMED_BY_CALLER: caller explicitly "
            "confirmed it after you repeated it back. DECLINED_BY_CALLER: caller refused to "
            "answer (value must be null). PENDING_HUMAN_REVIEW: answer is ambiguous - put the "
            "caller's words in note."
        )
    )
    value: str | None = Field(
        default=None,
        max_length=2000,
        description=(
            "Normalized answer. DATE fields: exact YYYY-MM-DD. CHOICE fields: one allowed code. "
            "Null for DECLINED_BY_CALLER."
        ),
    )
    note: str | None = Field(
        default=None, max_length=1000, description="Caller's own words when ambiguous or refused."
    )


class CheckAvailabilityInput(ToolInput):
    matter_category: str | None = Field(
        default=None,
        description="Matter category code; defaults to the category saved on the intake.",
    )
    limit: int = Field(default=3, ge=1, le=5, description="How many slots to offer.")


class BookConsultationInput(ToolInput):
    slot_id: uuid.UUID = Field(description="A slot_id returned by check_appointment_availability.")


class MarkForHumanReviewInput(ToolInput):
    reason: Literal["CONFIGURED_RULE", "UNABLE_TO_COMPLETE"]
    rule_id: str | None = Field(
        default=None, description="Firm escalation rule id (only for CONFIGURED_RULE)."
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Why the interaction cannot be completed (only for UNABLE_TO_COMPLETE).",
    )


class FinalizeIntakeSummaryInput(ToolInput):
    summary: str = Field(
        min_length=1,
        max_length=2000,
        description="Neutral factual summary of what the caller reported. No legal assessment.",
    )


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None

    @classmethod
    def success(cls, data: dict[str, Any]) -> "ToolResult":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: ToolError) -> "ToolResult":
        return cls(ok=False, error=error)

    def to_json(self) -> str:
        if self.ok:
            return json.dumps({"ok": True, "data": self.data}, default=str)
        assert self.error is not None
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "code": self.error.code,
                    "message": self.error.message,
                    "retryable": self.error.retryable,
                    "details": self.error.details,
                },
            },
            default=str,
        )
