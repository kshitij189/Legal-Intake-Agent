"""Human-review queue. An escalation is a persisted record, never just a spoken promise.

`CONFIGURED_RULE` escalations are only accepted when the firm rule matches the persisted intake
state; acceptance and resolution are staff-only actions.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import policy_engine
from app.domain.actors import Action, Actor, authorize
from app.domain.audit import record_audit
from app.domain.clock import resolve_now
from app.domain.errors import (
    IdempotencyConflict,
    NotFound,
    PermissionDenied,
    RuleNotMatched,
    ValidationFailed,
)
from app.domain.firm_config_service import firm_today, get_active_firm, load_config
from app.domain.idempotency import validate_idempotency_key
from app.domain.intake_service import get_intake, intake_fields
from app.domain.transitions import ensure_escalation_transition
from app.domain.workflow_service import handoff_workflow_id, request_workflow
from app.models import Escalation
from app.models.enums import ActorType, EscalationReason, EscalationStatus, Priority, WorkflowType

ENTITY = "escalation"
# Not tied to a firm rule, so the priority is fixed in code rather than chosen by the model.
FIXED_PRIORITIES = {
    EscalationReason.UNABLE_TO_COMPLETE: Priority.HIGH,
    EscalationReason.INCOMPLETE_INTAKE: Priority.NORMAL,
}
UNABLE_TO_COMPLETE_PRIORITY = FIXED_PRIORITIES[EscalationReason.UNABLE_TO_COMPLETE]
MAX_TEXT_LENGTH = 1000


@dataclass(frozen=True)
class EscalationResult:
    escalation: Escalation
    created: bool  # False when an existing open escalation (or idempotent replay) was returned


def evaluate_escalation_rules(
    session: Session, *, firm_id: uuid.UUID, intake_id: uuid.UUID, now: datetime | None = None
) -> list[policy_engine.RuleMatch]:
    now = resolve_now(now)
    firm = get_active_firm(session, firm_id)
    intake = get_intake(session, firm_id=firm_id, intake_id=intake_id)
    return policy_engine.evaluate_rules(
        load_config(firm), intake_fields(intake), firm_today(firm, now)
    )


def rule_escalation_key(intake_id: object, rule_id: str) -> str:
    """Shared by automatic and requested rule escalations, so they never duplicate."""
    return f"rule:{intake_id}:{rule_id}"


def apply_escalation_rules(
    session: Session,
    *,
    firm_id: uuid.UUID,
    intake_id: uuid.UUID,
    actor: Actor,
    now: datetime | None = None,
) -> list[tuple[policy_engine.RuleMatch, "EscalationResult"]]:
    """Evaluate the firm's rules on the stored intake and create (or return the existing open)
    escalation for every match. Called after any field change, by the agent or by staff."""
    now = resolve_now(now)
    return [
        (
            match,
            create_escalation(
                session,
                firm_id=firm_id,
                actor=actor,
                intake_id=intake_id,
                reason_code=EscalationReason.CONFIGURED_RULE,
                rule_id=match.rule_id,
                idempotency_key=rule_escalation_key(intake_id, match.rule_id),
                now=now,
            ),
        )
        for match in evaluate_escalation_rules(
            session, firm_id=firm_id, intake_id=intake_id, now=now
        )
    ]


def create_escalation(
    session: Session,
    *,
    firm_id: uuid.UUID,
    actor: Actor,
    intake_id: uuid.UUID,
    reason_code: EscalationReason,
    idempotency_key: str,
    rule_id: str | None = None,
    description: str | None = None,
    now: datetime | None = None,
) -> EscalationResult:
    now = resolve_now(now)
    validate_idempotency_key(idempotency_key)
    authorize(session, firm_id, actor, Action.ESCALATION_CREATE)
    firm = get_active_firm(session, firm_id)
    config = load_config(firm)
    intake = get_intake(session, firm_id=firm_id, intake_id=intake_id, for_update=True)

    replay = session.scalar(
        select(Escalation).where(
            Escalation.firm_id == firm_id, Escalation.idempotency_key == idempotency_key
        )
    )
    if replay is not None:
        if (replay.intake_id, replay.reason_code, replay.rule_id) == (
            intake_id,
            reason_code,
            rule_id,
        ):
            return EscalationResult(replay, created=False)
        raise IdempotencyConflict("idempotency key was already used for a different escalation")

    if reason_code is EscalationReason.CONFIGURED_RULE:
        if rule_id is None:
            raise ValidationFailed("CONFIGURED_RULE escalations need a rule_id")
        rule = config.rule(rule_id)
        if rule is None:
            raise ValidationFailed("unknown escalation rule", rule_id=rule_id)
        match = policy_engine.evaluate_rule(rule, intake_fields(intake), firm_today(firm, now))
        if match is None:
            raise RuleNotMatched("the rule does not match the stored intake state", rule_id=rule_id)
        open_for_rule = session.scalar(
            select(Escalation).where(
                Escalation.intake_id == intake_id,
                Escalation.rule_id == rule_id,
                Escalation.status != EscalationStatus.RESOLVED,
            )
        )
        if open_for_rule is not None:
            return EscalationResult(open_for_rule, created=False)
        priority, reason_text = rule.priority, rule.description
        details: dict[str, str] = {"rule_id": rule_id, "field_key": match.field_key}
    else:
        if rule_id is not None:
            raise ValidationFailed("rule_id is only valid for CONFIGURED_RULE escalations")
        if reason_code is EscalationReason.INCOMPLETE_INTAKE and actor.type is not ActorType.SYSTEM:
            raise PermissionDenied("only the system raises INCOMPLETE_INTAKE follow-ups")
        reason_text = (description or "").strip()
        if not reason_text or len(reason_text) > MAX_TEXT_LENGTH:
            raise ValidationFailed(
                f"{reason_code} needs a description of 1-{MAX_TEXT_LENGTH} characters"
            )
        priority = FIXED_PRIORITIES[reason_code]
        details = {}

    escalation = Escalation(
        firm_id=firm_id,
        intake_id=intake_id,
        reason_code=reason_code,
        rule_id=rule_id,
        reason_text=reason_text,
        priority=priority,
        status=EscalationStatus.PENDING,
        idempotency_key=idempotency_key,
    )
    session.add(escalation)
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="escalation.created",
        entity_type=ENTITY,
        entity_id=escalation.id,
        details={
            "intake_id": str(intake_id),
            "reason_code": str(reason_code),
            "priority": str(priority),
            **details,
        },
    )
    # Outbox: the durable handoff (reviewer alert, reminders, fallback) is requested atomically
    # with the escalation, so it cannot be lost if Temporal is unreachable right now.
    request_workflow(
        session,
        firm_id=firm_id,
        workflow_id=handoff_workflow_id(escalation.id),
        workflow_type=WorkflowType.HANDOFF,
        entity_type=ENTITY,
        entity_id=escalation.id,
    )
    return EscalationResult(escalation, created=True)


def get_escalation(
    session: Session,
    *,
    firm_id: uuid.UUID,
    escalation_id: uuid.UUID,
    for_update: bool = False,
) -> Escalation:
    """Authoritative handoff state; never report acceptance unless this says ACCEPTED."""
    stmt = select(Escalation).where(Escalation.id == escalation_id, Escalation.firm_id == firm_id)
    if for_update:
        stmt = stmt.with_for_update()
    escalation = session.scalar(stmt)
    if escalation is None:
        raise NotFound("escalation not found", escalation_id=str(escalation_id))
    return escalation


def list_escalations(
    session: Session,
    *,
    firm_id: uuid.UUID,
    statuses: Collection[EscalationStatus] | None = None,
    intake_id: uuid.UUID | None = None,
) -> list[Escalation]:
    stmt = select(Escalation).where(Escalation.firm_id == firm_id)
    if intake_id is not None:
        stmt = stmt.where(Escalation.intake_id == intake_id)
    if statuses is not None:
        stmt = stmt.where(Escalation.status.in_(list(statuses)))
    return list(session.scalars(stmt.order_by(Escalation.created_at, Escalation.id)))


def _transition(
    session: Session,
    *,
    firm_id: uuid.UUID,
    escalation_id: uuid.UUID,
    actor: Actor,
    target: EscalationStatus,
    action: Action,
    now: datetime,
    note: str | None = None,
) -> Escalation:
    staff = authorize(session, firm_id, actor, action)
    escalation = get_escalation(
        session, firm_id=firm_id, escalation_id=escalation_id, for_update=True
    )
    previous = escalation.status
    ensure_escalation_transition(previous, target)

    escalation.status = target
    if target is EscalationStatus.ACCEPTED:
        assert staff is not None  # accept is staff-only
        escalation.assigned_staff_id = staff.id
        escalation.accepted_at = now
    elif target is EscalationStatus.RESOLVED:
        escalation.resolution_note = note
        escalation.resolved_at = now
    elif target is EscalationStatus.FAILED:
        escalation.last_error = note
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action=f"escalation.{target.lower()}",
        entity_type=ENTITY,
        entity_id=escalation.id,
        details={"from": str(previous), "to": str(target)},
    )
    return escalation


def _required_text(text: str, what: str) -> str:
    clean = text.strip()
    if not clean or len(clean) > MAX_TEXT_LENGTH:
        raise ValidationFailed(f"{what} must be 1-{MAX_TEXT_LENGTH} characters")
    return clean


def accept_escalation(
    session: Session,
    *,
    firm_id: uuid.UUID,
    escalation_id: uuid.UUID,
    actor: Actor,
    now: datetime | None = None,
) -> Escalation:
    return _transition(
        session,
        firm_id=firm_id,
        escalation_id=escalation_id,
        actor=actor,
        target=EscalationStatus.ACCEPTED,
        action=Action.ESCALATION_ACCEPT,
        now=resolve_now(now),
    )


def resolve_escalation(
    session: Session,
    *,
    firm_id: uuid.UUID,
    escalation_id: uuid.UUID,
    actor: Actor,
    resolution_note: str,
    now: datetime | None = None,
) -> Escalation:
    return _transition(
        session,
        firm_id=firm_id,
        escalation_id=escalation_id,
        actor=actor,
        target=EscalationStatus.RESOLVED,
        action=Action.ESCALATION_RESOLVE,
        now=resolve_now(now),
        note=_required_text(resolution_note, "resolution note"),
    )


def mark_escalation_failed(
    session: Session,
    *,
    firm_id: uuid.UUID,
    escalation_id: uuid.UUID,
    actor: Actor,
    error: str,
    now: datetime | None = None,
) -> Escalation:
    """Record that the handoff could not be delivered (used by workflows in Phase 5)."""
    return _transition(
        session,
        firm_id=firm_id,
        escalation_id=escalation_id,
        actor=actor,
        target=EscalationStatus.FAILED,
        action=Action.ESCALATION_MARK_FAILED,
        now=resolve_now(now),
        note=_required_text(error, "error"),
    )
