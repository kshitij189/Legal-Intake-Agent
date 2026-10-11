"""Conversation sessions. Ending a session early keeps partial intake state (-> INCOMPLETE)."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.actors import Action, Actor, authorize
from app.domain.audit import record_audit
from app.domain.clock import resolve_now
from app.domain.errors import InvalidTransition, NotFound, ValidationFailed
from app.domain.firm_config_service import get_active_firm
from app.domain.intake_service import get_intake, transition_intake
from app.models import CallSession
from app.models.enums import CallSessionStatus, IntakeStatus, SessionChannel

ENTITY = "call_session"
_ENDED_EARLY = {CallSessionStatus.DISCONNECTED, CallSessionStatus.FAILED}


def start_call_session(
    session: Session, *, firm_id: uuid.UUID, actor: Actor, channel: SessionChannel
) -> CallSession:
    authorize(session, firm_id, actor, Action.SESSION_MANAGE)
    get_active_firm(session, firm_id)
    call = CallSession(firm_id=firm_id, channel=channel, status=CallSessionStatus.ACTIVE)
    session.add(call)
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="session.started",
        entity_type=ENTITY,
        entity_id=call.id,
        details={"channel": str(channel)},
    )
    return call


def get_call_session(
    session: Session, *, firm_id: uuid.UUID, call_session_id: uuid.UUID, for_update: bool = False
) -> CallSession:
    stmt = select(CallSession).where(
        CallSession.id == call_session_id, CallSession.firm_id == firm_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    call = session.scalar(stmt)
    if call is None:
        raise NotFound("call session not found", call_session_id=str(call_session_id))
    return call


def attach_intake(
    session: Session,
    *,
    firm_id: uuid.UUID,
    call_session_id: uuid.UUID,
    intake_id: uuid.UUID,
    actor: Actor,
) -> CallSession:
    authorize(session, firm_id, actor, Action.SESSION_MANAGE)
    call = get_call_session(
        session, firm_id=firm_id, call_session_id=call_session_id, for_update=True
    )
    if call.status is not CallSessionStatus.ACTIVE:
        raise InvalidTransition("session is not active", current=str(call.status))
    if call.intake_id is not None and call.intake_id != intake_id:
        raise ValidationFailed("session already has a different intake")
    get_intake(session, firm_id=firm_id, intake_id=intake_id)  # same tenant
    call.intake_id = intake_id
    session.flush()
    return call


def end_call_session(
    session: Session,
    *,
    firm_id: uuid.UUID,
    call_session_id: uuid.UUID,
    actor: Actor,
    status: CallSessionStatus,
    reason: str | None = None,
    now: datetime | None = None,
) -> CallSession:
    """End an active session. DISCONNECTED/FAILED mark an in-progress intake INCOMPLETE."""
    now = resolve_now(now)
    authorize(session, firm_id, actor, Action.SESSION_MANAGE)
    if status is CallSessionStatus.ACTIVE:
        raise ValidationFailed("a session cannot be ended as ACTIVE")
    call = get_call_session(
        session, firm_id=firm_id, call_session_id=call_session_id, for_update=True
    )
    if call.status is not CallSessionStatus.ACTIVE:
        raise InvalidTransition("session has already ended", current=str(call.status))

    call.status = status
    call.ended_at = now
    call.end_reason = reason[:200] if reason else None
    if status in _ENDED_EARLY and call.intake_id is not None:
        intake = get_intake(session, firm_id=firm_id, intake_id=call.intake_id)
        if intake.status is IntakeStatus.IN_PROGRESS:
            transition_intake(
                session,
                firm_id=firm_id,
                intake_id=intake.id,
                actor=actor,
                target=IntakeStatus.INCOMPLETE,
            )
    session.flush()
    record_audit(
        session,
        firm_id=firm_id,
        actor=actor,
        action="session.ended",
        entity_type=ENTITY,
        entity_id=call.id,
        details={"status": str(status), "reason": call.end_reason},
    )
    return call
