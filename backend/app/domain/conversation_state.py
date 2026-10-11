"""Structured conversation state derived from persisted data (blueprint §8).

The agent is shown this state on every model request, so it always knows what is saved, what is
missing, what needs review, and the next step, even after interruptions or corrections. It is
recomputed from the database each time; nothing here is remembered by the model alone.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.domain import appointment_service, escalation_service, intake_service, policy_engine
from app.domain.firm_config_service import get_active_firm, load_config
from app.models.enums import FieldStatus, IntakeStatus
from app.schemas.firm_config import FirmConfig
from app.schemas.intake import FieldEntry

CREATE_DRAFT_INTAKE = "CREATE_DRAFT_INTAKE"
INTAKE_SUBMITTED = "INTAKE_SUBMITTED_CLOSE_POLITELY"
FINALIZE_WHEN_DONE = "ALL_REQUIRED_COLLECTED_FINALIZE_WHEN_CALLER_IS_DONE"


@dataclass(frozen=True)
class FieldState:
    key: str
    required: bool
    value: str | None
    status: FieldStatus


@dataclass(frozen=True)
class ConversationState:
    intake_reference: str | None
    intake_status: IntakeStatus | None
    fields: tuple[FieldState, ...]
    missing_required: tuple[str, ...]
    declined: tuple[str, ...]
    needs_review: tuple[str, ...]
    escalations: tuple[tuple[str, str], ...]  # (rule id or reason, status)
    appointment: str | None  # ISO start time of the booked consultation
    next_action: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "intake_reference": self.intake_reference,
            "status": self.intake_status,
            "missing_required_fields": list(self.missing_required),
            "declined_fields": list(self.declined),
            "fields_pending_human_review": list(self.needs_review),
            "escalation": {
                "required": bool(self.escalations),
                "items": [{"reason": r, "status": s} for r, s in self.escalations],
            },
            "booked_appointment": self.appointment,
            "next_action": self.next_action,
        }

    def to_prompt(self) -> str:
        if self.intake_reference is None:
            return f"Current intake state: no draft intake yet. Next action: {self.next_action}."
        saved = "; ".join(
            f"{f.key}={f.value!r} [{f.status}]" if f.value is not None else f"{f.key} [{f.status}]"
            for f in self.fields
            if f.status is not FieldStatus.MISSING
        )
        escalations = ", ".join(f"{r} ({s})" for r, s in self.escalations) or "none"
        return (
            "Current intake state (authoritative, from the database - trust it over memory):\n"
            f"- intake {self.intake_reference}, status {self.intake_status}\n"
            f"- saved: {saved or 'nothing yet'}\n"
            f"- missing required: {', '.join(self.missing_required) or 'none'}\n"
            f"- declined by caller: {', '.join(self.declined) or 'none'}\n"
            f"- pending human review: {', '.join(self.needs_review) or 'none'}\n"
            f"- escalations for human review: {escalations}\n"
            f"- booked consultation: {self.appointment or 'none'}\n"
            f"- next action: {self.next_action}"
        )


def next_action(config: FirmConfig, status: IntakeStatus | None, missing: tuple[str, ...]) -> str:
    if status is None:
        return CREATE_DRAFT_INTAKE
    if status in (IntakeStatus.PENDING_REVIEW, IntakeStatus.CLOSED):
        return INTAKE_SUBMITTED
    if missing:
        return f"COLLECT_{missing[0].upper()}"
    return FINALIZE_WHEN_DONE


def derive_state(
    config: FirmConfig,
    *,
    reference: str | None,
    status: IntakeStatus | None,
    fields: dict[str, FieldEntry],
    escalations: tuple[tuple[str, str], ...] = (),
    appointment: str | None = None,
) -> ConversationState:
    completeness = policy_engine.completeness(config, fields)
    return ConversationState(
        intake_reference=reference,
        intake_status=status,
        fields=tuple(
            FieldState(
                key=q.key,
                required=q.required,
                value=fields.get(q.key, FieldEntry()).value,
                status=fields.get(q.key, FieldEntry()).status,
            )
            for q in config.intake_questions
        ),
        missing_required=completeness.missing,
        declined=completeness.declined,
        needs_review=completeness.needs_review,
        escalations=escalations,
        appointment=appointment,
        next_action=next_action(config, status, completeness.missing if status else ()),
    )


def build_conversation_state(
    session: Session, *, firm_id: uuid.UUID, intake_id: uuid.UUID | None
) -> ConversationState:
    config = load_config(get_active_firm(session, firm_id))
    if intake_id is None:
        return derive_state(config, reference=None, status=None, fields={})
    intake = intake_service.get_intake(session, firm_id=firm_id, intake_id=intake_id)
    escalations = tuple(
        (e.rule_id or str(e.reason_code), str(e.status))
        for e in escalation_service.list_escalations(session, firm_id=firm_id, intake_id=intake_id)
    )
    appointment = appointment_service.get_booked_appointment_for_intake(
        session, firm_id=firm_id, intake_id=intake_id
    )
    return derive_state(
        config,
        reference=intake.reference,
        status=intake.status,
        fields=intake_service.intake_fields(intake),
        escalations=escalations,
        appointment=appointment.starts_at.isoformat() if appointment else None,
    )
