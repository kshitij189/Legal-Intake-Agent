"""Human-review tools. Results only ever report the persisted handoff status."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.domain import escalation_service
from app.models.enums import EscalationReason, EscalationStatus
from app.tools.context import ToolContext, require_intake
from app.tools.schemas import MarkForHumanReviewInput, NoArguments

_STATUS_MEANING = {
    EscalationStatus.PENDING: "Queued for human review; nobody has accepted it yet.",
    EscalationStatus.ACCEPTED: "A staff member has accepted it.",
    EscalationStatus.FAILED: "The handoff could not be delivered.",
    EscalationStatus.RESOLVED: "Resolved by staff.",
}


def unable_escalation_key(call_session_id: uuid.UUID) -> str:
    """One UNABLE_TO_COMPLETE escalation per conversation (tool and system fallback share it)."""
    return f"unable:{call_session_id}"


def mark_for_human_review(
    session: Session, ctx: ToolContext, args: MarkForHumanReviewInput
) -> dict[str, Any]:
    intake_id = require_intake(ctx)
    reason = EscalationReason(args.reason)
    if reason is EscalationReason.CONFIGURED_RULE:
        key = escalation_service.rule_escalation_key(intake_id, args.rule_id or "")
    else:
        key = unable_escalation_key(ctx.call_session_id)
    result = escalation_service.create_escalation(
        session,
        firm_id=ctx.firm_id,
        actor=ctx.actor,
        intake_id=intake_id,
        reason_code=reason,
        rule_id=args.rule_id,
        description=args.description,
        idempotency_key=key,
        now=ctx.clock(),
    )
    return {"escalation_id": str(result.escalation.id), "newly_created": result.created}


def verify_escalation(session: Session, ctx: ToolContext, data: dict[str, Any]) -> dict[str, Any]:
    escalation = escalation_service.get_escalation(
        session, firm_id=ctx.firm_id, escalation_id=uuid.UUID(data["escalation_id"])
    )
    return {
        **data,
        "status": escalation.status,
        "priority": escalation.priority,
        "meaning": _STATUS_MEANING[escalation.status],
    }


def get_handoff_status(session: Session, ctx: ToolContext, _: NoArguments) -> dict[str, Any]:
    intake_id = require_intake(ctx)
    escalations = escalation_service.list_escalations(
        session, firm_id=ctx.firm_id, intake_id=intake_id
    )
    return {
        "escalations": [
            {
                "escalation_id": str(e.id),
                "reason": e.reason_code,
                "rule_id": e.rule_id,
                "priority": e.priority,
                "status": e.status,
                "meaning": _STATUS_MEANING[e.status],
                "accepted_by_staff": e.status is EscalationStatus.ACCEPTED,
            }
            for e in escalations
        ]
    }
