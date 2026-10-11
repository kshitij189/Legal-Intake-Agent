"""Intake tools. Handlers run inside the executor's unit of work and only call domain services."""

from typing import Any

from sqlalchemy.orm import Session

from app.domain import (
    appointment_service,
    conversation_state,
    escalation_service,
    intake_service,
    policy_engine,
)
from app.domain.call_session_service import attach_intake
from app.domain.firm_config_service import firm_today, get_active_firm, load_config
from app.models.enums import EscalationReason, FieldStatus
from app.tools.context import ToolContext, require_intake
from app.tools.schemas import FinalizeIntakeSummaryInput, NoArguments, SaveIntakeAnswerInput


def rule_escalation_key(intake_id: object, rule_id: str) -> str:
    """Shared by automatic and model-requested rule escalations, so they never duplicate."""
    return f"rule:{intake_id}:{rule_id}"


def get_intake_questions(session: Session, ctx: ToolContext, _: NoArguments) -> dict[str, Any]:
    config = load_config(get_active_firm(session, ctx.firm_id))
    return {
        "questions": [
            {
                "key": q.key,
                "prompt": q.prompt,
                "type": str(q.type),
                "required": q.required,
                "choices": [{"code": c.code, "label": c.label} for c in q.choices],
            }
            for q in config.intake_questions
        ]
    }


def create_draft_intake(session: Session, ctx: ToolContext, _: NoArguments) -> dict[str, Any]:
    """Idempotent per conversation: a second call returns the existing draft."""
    if ctx.intake_id is not None:
        intake = intake_service.get_intake(session, firm_id=ctx.firm_id, intake_id=ctx.intake_id)
        return {"intake_reference": intake.reference, "status": intake.status, "created": False}
    intake = intake_service.create_draft_intake(
        session, firm_id=ctx.firm_id, actor=ctx.actor, now=ctx.clock()
    )
    attach_intake(
        session,
        firm_id=ctx.firm_id,
        call_session_id=ctx.call_session_id,
        intake_id=intake.id,
        actor=ctx.actor,
    )
    return {"intake_reference": intake.reference, "status": intake.status, "created": True}


def save_intake_answer(
    session: Session, ctx: ToolContext, args: SaveIntakeAnswerInput
) -> dict[str, Any]:
    intake_id = require_intake(ctx)
    now = ctx.clock()
    intake = intake_service.save_intake_answer(
        session,
        firm_id=ctx.firm_id,
        intake_id=intake_id,
        actor=ctx.actor,
        field_key=args.field_key,
        status=FieldStatus(args.status),
        value=args.value,
        note=args.note,
        now=now,
    )
    firm = get_active_firm(session, ctx.firm_id)
    config = load_config(firm)
    fields = intake_service.intake_fields(intake)

    # Escalation rules are applied deterministically here; the model is told the outcome.
    escalations = []
    for match in policy_engine.evaluate_rules(config, fields, firm_today(firm, now)):
        result = escalation_service.create_escalation(
            session,
            firm_id=ctx.firm_id,
            actor=ctx.actor,
            intake_id=intake_id,
            reason_code=EscalationReason.CONFIGURED_RULE,
            rule_id=match.rule_id,
            idempotency_key=rule_escalation_key(intake_id, match.rule_id),
            now=now,
        )
        escalations.append(
            {
                "rule_id": match.rule_id,
                "description": match.description,
                "priority": result.escalation.priority,
                "status": result.escalation.status,
                "newly_created": result.created,
            }
        )
    saved = fields[args.field_key]
    return {
        "field_key": args.field_key,
        "saved": {"value": saved.value, "status": saved.status},
        "escalations_for_human_review": escalations,
        "missing_required_fields": list(policy_engine.completeness(config, fields).missing),
    }


def get_intake_status(session: Session, ctx: ToolContext, _: NoArguments) -> dict[str, Any]:
    """Authoritative persisted state of this conversation's intake."""
    intake_id = require_intake(ctx)
    intake = intake_service.get_intake(session, firm_id=ctx.firm_id, intake_id=intake_id)
    config = load_config(get_active_firm(session, ctx.firm_id))
    fields = intake_service.intake_fields(intake)
    completeness = policy_engine.completeness(config, fields)
    escalations = escalation_service.list_escalations(
        session, firm_id=ctx.firm_id, intake_id=intake_id
    )
    appointment = appointment_service.get_booked_appointment_for_intake(
        session, firm_id=ctx.firm_id, intake_id=intake_id
    )
    return {
        "intake_reference": intake.reference,
        "status": intake.status,
        "fields": {k: {"value": e.value, "status": e.status} for k, e in fields.items()},
        "missing_required_fields": list(completeness.missing),
        "declined_fields": list(completeness.declined),
        "fields_pending_human_review": list(completeness.needs_review),
        "escalations": [
            {"rule_id": e.rule_id, "reason": e.reason_code, "status": e.status} for e in escalations
        ],
        "booked_appointment": (
            {"appointment_id": str(appointment.id), "starts_at": appointment.starts_at.isoformat()}
            if appointment
            else None
        ),
        "next_action": conversation_state.next_action(config, intake.status, completeness.missing),
    }


def finalize_intake_summary(
    session: Session, ctx: ToolContext, args: FinalizeIntakeSummaryInput
) -> dict[str, Any]:
    intake_id = require_intake(ctx)
    intake, completeness = intake_service.finalize_intake(
        session, firm_id=ctx.firm_id, intake_id=intake_id, actor=ctx.actor, summary=args.summary
    )
    return {
        "intake_reference": intake.reference,
        "status": intake.status,
        "missing_required_fields": list(completeness.missing),
        "declined_fields": list(completeness.declined),
        "fields_pending_human_review": list(completeness.needs_review),
        "note": "The intake is now waiting for review by the firm's staff.",
    }
