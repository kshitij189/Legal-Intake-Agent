"""Draft intake records: creation, field capture with provenance, lifecycle transitions.

Services flush but never commit; the caller owns the transaction.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import policy_engine
from app.domain.actors import Action, Actor, authorize
from app.domain.audit import record_audit
from app.domain.clock import resolve_now
from app.domain.errors import InvalidTransition, NotFound, PermissionDenied, ValidationFailed
from app.domain.firm_config_service import get_active_firm, load_config
from app.domain.transitions import ensure_intake_transition
from app.models import Escalation, IntakeRecord, intake_reference_seq
from app.models.enums import ActorType, EscalationStatus, FieldStatus, IntakeStatus
from app.schemas.intake import FieldEntry

ENTITY = "intake"
_EDITABLE = {IntakeStatus.IN_PROGRESS, IntakeStatus.INCOMPLETE}
_STAFF_EDITABLE = _EDITABLE | {IntakeStatus.PENDING_REVIEW}


def create_draft_intake(
    session: Session, *, firm_id: uuid.UUID, actor: Actor, now: datetime | None = None
) -> IntakeRecord:
    now = resolve_now(now)
    authorize(session, firm_id, actor, Action.INTAKE_CREATE)
    config = load_config(get_active_firm(session, firm_id))

    number = session.scalar(select(intake_reference_seq.next_value()))
    intake = IntakeRecord(
        firm_id=firm_id,
        reference=f"INT-{number}",
        status=IntakeStatus.IN_PROGRESS,
        fields=policy_engine.dump_fields(policy_engine.initial_fields(config)),
    )
    session.add(intake)
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="intake.created",
        entity_type=ENTITY,
        entity_id=intake.id,
        details={"reference": intake.reference},
    )
    return intake


def get_intake(
    session: Session, *, firm_id: uuid.UUID, intake_id: uuid.UUID, for_update: bool = False
) -> IntakeRecord:
    """Tenant-scoped lookup: another firm's intake is indistinguishable from a missing one."""
    stmt = select(IntakeRecord).where(IntakeRecord.id == intake_id, IntakeRecord.firm_id == firm_id)
    if for_update:
        stmt = stmt.with_for_update()
    intake = session.scalar(stmt)
    if intake is None:
        raise NotFound("intake not found", intake_id=str(intake_id))
    return intake


def intake_fields(intake: IntakeRecord) -> dict[str, FieldEntry]:
    return policy_engine.parse_fields(intake.fields)


def save_intake_answer(
    session: Session,
    *,
    firm_id: uuid.UUID,
    intake_id: uuid.UUID,
    actor: Actor,
    field_key: str,
    status: FieldStatus,
    value: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> IntakeRecord:
    now = resolve_now(now)
    authorize(session, firm_id, actor, Action.INTAKE_UPDATE)
    if status is FieldStatus.VERIFIED_BY_SYSTEM:
        # The agent can never mark caller statements as verified.
        authorize(session, firm_id, actor, Action.INTAKE_VERIFY_FIELD)

    config = load_config(get_active_firm(session, firm_id))
    question = config.question(field_key)
    if question is None:
        raise ValidationFailed("unknown intake field", field_key=field_key)

    intake = get_intake(session, firm_id=firm_id, intake_id=intake_id, for_update=True)
    editable = _STAFF_EDITABLE if actor.type is ActorType.STAFF else _EDITABLE
    if intake.status not in editable:
        raise InvalidTransition(
            f"intake in status {intake.status} cannot be edited", current=str(intake.status)
        )

    fields = intake_fields(intake)
    previous = fields.get(field_key, FieldEntry())
    if previous.status is FieldStatus.VERIFIED_BY_SYSTEM:
        try:
            authorize(session, firm_id, actor, Action.INTAKE_VERIFY_FIELD)
        except PermissionDenied as exc:
            raise PermissionDenied(
                "a verified value can only be changed by a staff reviewer", field_key=field_key
            ) from exc

    entry = policy_engine.build_field_entry(
        question, status=status, value=value, note=note, source=actor.type, now=now
    )
    previous_status = previous.status
    fields[field_key] = policy_engine.merge_with_previous(previous, entry)
    intake.fields = policy_engine.dump_fields(fields)  # reassign so the JSONB change is tracked
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="intake.field_saved",
        entity_type=ENTITY,
        entity_id=intake.id,
        # Field key and statuses only - values may contain contact details.
        details={
            "field_key": field_key,
            "previous_status": str(previous_status),
            "status": str(status),
            "corrected": len(fields[field_key].history) > len(previous.history),
        },
    )
    return intake


def transition_intake(
    session: Session,
    *,
    firm_id: uuid.UUID,
    intake_id: uuid.UUID,
    actor: Actor,
    target: IntakeStatus,
) -> IntakeRecord:
    action = Action.INTAKE_CLOSE if target is IntakeStatus.CLOSED else Action.INTAKE_TRANSITION
    authorize(session, firm_id, actor, action)

    intake = get_intake(session, firm_id=firm_id, intake_id=intake_id, for_update=True)
    previous = intake.status
    ensure_intake_transition(previous, target)
    if target is IntakeStatus.CLOSED:
        unresolved = session.scalar(
            select(func.count())
            .select_from(Escalation)
            .where(
                Escalation.intake_id == intake.id,
                Escalation.status != EscalationStatus.RESOLVED,
            )
        )
        if unresolved:
            # Closing would hide a pending (possibly urgent) handoff from the review queue.
            raise InvalidTransition(
                "resolve the intake's open escalations before closing it", open=unresolved
            )
    intake.status = target
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="intake.status_changed",
        entity_type=ENTITY,
        entity_id=intake.id,
        details={"from": str(previous), "to": str(target)},
    )
    return intake


MAX_SUMMARY_LENGTH = 2000


def finalize_intake(
    session: Session,
    *,
    firm_id: uuid.UUID,
    intake_id: uuid.UUID,
    actor: Actor,
    summary: str,
) -> tuple[IntakeRecord, policy_engine.Completeness]:
    """Store the summary and hand the draft to human review (status PENDING_REVIEW).

    The summary is validated against the record: something must have been captured, and exact
    dates, emails and phone numbers in it must match stored values. Missing, declined and
    pending-review fields do not block finalization; they are persisted as `outstanding` so the
    reviewer knows what is still open."""
    authorize(session, firm_id, actor, Action.INTAKE_TRANSITION)
    clean = summary.strip()
    if not clean or len(clean) > MAX_SUMMARY_LENGTH:
        raise ValidationFailed(f"summary must be 1-{MAX_SUMMARY_LENGTH} characters")
    config = load_config(get_active_firm(session, firm_id))

    intake = get_intake(session, firm_id=firm_id, intake_id=intake_id, for_update=True)
    previous = intake.status
    ensure_intake_transition(previous, IntakeStatus.PENDING_REVIEW)
    fields = intake_fields(intake)
    if all(e.status is FieldStatus.MISSING for e in fields.values()):
        raise ValidationFailed("nothing has been captured yet; save the caller's answers first")
    unsupported = policy_engine.unsupported_summary_facts(clean, fields)
    if unsupported:
        raise ValidationFailed(
            "the summary contains facts that are not in the intake record; save them as answers"
            " first or remove them from the summary",
            unsupported=unsupported,
        )

    result = policy_engine.completeness(config, fields)
    intake.summary = clean
    intake.status = IntakeStatus.PENDING_REVIEW
    intake.outstanding = {
        "missing": list(result.missing),
        "declined": list(result.declined),
        "needs_review": list(result.needs_review),
    }
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="intake.finalized",
        entity_type=ENTITY,
        entity_id=intake.id,
        details={
            "from": str(previous),
            "to": str(IntakeStatus.PENDING_REVIEW),
            "missing": list(result.missing),
            "declined": list(result.declined),
            "needs_review": list(result.needs_review),
        },
    )
    return intake, result
