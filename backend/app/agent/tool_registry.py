"""The nine blueprint tools and their JSON-schema definitions for the model.

Schemas are generated from the Pydantic input models and narrowed with the firm's configuration
(field keys, category codes, rule ids). The executor re-validates every call regardless.
"""

import copy
from typing import Any

from app.schemas.firm_config import FirmConfig
from app.tools import calendar_tools, escalation_tools, intake_tools
from app.tools.executor import ToolSpec
from app.tools.schemas import (
    BookConsultationInput,
    CheckAvailabilityInput,
    FinalizeIntakeSummaryInput,
    MarkForHumanReviewInput,
    NoArguments,
    SaveIntakeAnswerInput,
    ToolInput,
)

_SPECS = (
    ToolSpec(
        "get_intake_questions",
        "Return the firm's configured intake questions (keys, types, allowed choices).",
        NoArguments,
        intake_tools.get_intake_questions,
    ),
    ToolSpec(
        "create_draft_intake",
        "Create the draft intake record for this conversation. Call once, before saving answers. "
        "Safe to repeat: returns the existing draft.",
        NoArguments,
        intake_tools.create_draft_intake,
    ),
    ToolSpec(
        "save_intake_answer",
        "Save one answer with its provenance status as soon as the caller gives it. The result "
        "lists any escalations the firm's rules created and the required fields still missing.",
        SaveIntakeAnswerInput,
        intake_tools.save_intake_answer,
    ),
    ToolSpec(
        "get_intake_status",
        "Return the stored intake: field values and statuses, missing fields, escalations and any "
        "booked appointment. This is the authoritative state.",
        NoArguments,
        intake_tools.get_intake_status,
    ),
    ToolSpec(
        "check_appointment_availability",
        "Return real open consultation slots. Offer the caller only slots returned here.",
        CheckAvailabilityInput,
        calendar_tools.check_appointment_availability,
    ),
    ToolSpec(
        "book_consultation",
        "Book a slot the caller chose from check_appointment_availability. Confirm the "
        "appointment to the caller only if the result has verified=true.",
        BookConsultationInput,
        calendar_tools.book_consultation,
        verify=calendar_tools.verify_booking,
    ),
    ToolSpec(
        "mark_for_human_review",
        "Queue the intake for human review. Use CONFIGURED_RULE with a firm rule_id, or "
        "UNABLE_TO_COMPLETE with a description when you cannot safely complete the intake. "
        "A queued review is not an acceptance by a lawyer.",
        MarkForHumanReviewInput,
        escalation_tools.mark_for_human_review,
        verify=escalation_tools.verify_escalation,
    ),
    ToolSpec(
        "get_handoff_status",
        "Return the actual status of this intake's human-review requests.",
        NoArguments,
        escalation_tools.get_handoff_status,
    ),
    ToolSpec(
        "finalize_intake_summary",
        "Save a neutral factual summary and submit the intake for staff review. Call once, at "
        "the end of the conversation. No answers can be saved afterwards.",
        FinalizeIntakeSummaryInput,
        intake_tools.finalize_intake_summary,
    ),
)

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


def _strip_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def _restrict(schema: dict[str, Any], prop: str, values: list[str]) -> None:
    if not values:
        return
    target = schema["properties"][prop]
    options = target.get("anyOf", [target])
    for option in options:
        if option.get("type") == "string":
            option["enum"] = values


def _parameters(model: type[ToolInput], config: FirmConfig) -> dict[str, Any]:
    schema: dict[str, Any] = _strip_titles(copy.deepcopy(model.model_json_schema()))
    schema.setdefault("properties", {})
    schema["additionalProperties"] = False
    if model is SaveIntakeAnswerInput:
        _restrict(schema, "field_key", [q.key for q in config.intake_questions])
    elif model is CheckAvailabilityInput:
        _restrict(schema, "matter_category", [c.code for c in config.matter_categories])
    elif model is MarkForHumanReviewInput:
        _restrict(schema, "rule_id", [r.id for r in config.escalation_rules])
    return schema


def tool_definitions(config: FirmConfig) -> list[dict[str, Any]]:
    """OpenAI-compatible `tools` payload (the format Groq's chat API expects)."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": _parameters(spec.input_model, config),
            },
        }
        for spec in _SPECS
    ]
