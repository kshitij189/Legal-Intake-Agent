"""Transcript turns, retained only when the session's consent metadata allows it.

Transcripts contain personal data: they are never written to logs, and access is restricted to the
firm (tenant-scoped queries).
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.domain.actors import Actor
from app.domain.audit import record_audit
from app.domain.call_session_service import get_call_session
from app.domain.clock import resolve_now
from app.domain.firm_config_service import get_active_firm, load_config
from app.models import TranscriptTurn
from app.models.enums import Speaker

MAX_TURN_LENGTH = 4000


def transcripts_retained(
    session: Session, *, firm_id: uuid.UUID, call_session_id: uuid.UUID
) -> bool:
    call = get_call_session(session, firm_id=firm_id, call_session_id=call_session_id)
    return bool((call.consent or {}).get("transcript_retained"))


def record_turn(
    session: Session,
    *,
    firm_id: uuid.UUID,
    call_session_id: uuid.UUID,
    speaker: Speaker,
    text: str,
    processing_ms: int | None = None,
) -> TranscriptTurn | None:
    """Append a turn; returns None (and stores nothing) when transcripts are not retained."""
    if not transcripts_retained(session, firm_id=firm_id, call_session_id=call_session_id):
        return None
    # Lock the session row so concurrent writers cannot take the same sequence number.
    get_call_session(session, firm_id=firm_id, call_session_id=call_session_id, for_update=True)
    last = session.scalar(
        select(func.max(TranscriptTurn.seq)).where(
            TranscriptTurn.call_session_id == call_session_id
        )
    )
    turn = TranscriptTurn(
        firm_id=firm_id,
        call_session_id=call_session_id,
        seq=(last or 0) + 1,
        speaker=speaker,
        text=text[:MAX_TURN_LENGTH],
        processing_ms=processing_ms,
    )
    session.add(turn)
    session.flush()
    return turn


def mark_last_agent_turn_interrupted(
    session: Session, *, firm_id: uuid.UUID, call_session_id: uuid.UUID
) -> TranscriptTurn | None:
    turn = session.scalar(
        select(TranscriptTurn)
        .where(
            TranscriptTurn.firm_id == firm_id,
            TranscriptTurn.call_session_id == call_session_id,
            TranscriptTurn.speaker == Speaker.AGENT,
        )
        .order_by(TranscriptTurn.seq.desc())
        .limit(1)
    )
    if turn is not None:
        turn.interrupted = True
        session.flush()
    return turn


def list_turns(
    session: Session, *, firm_id: uuid.UUID, call_session_id: uuid.UUID
) -> list[TranscriptTurn]:
    return list(
        session.scalars(
            select(TranscriptTurn)
            .where(
                TranscriptTurn.firm_id == firm_id,
                TranscriptTurn.call_session_id == call_session_id,
            )
            .order_by(TranscriptTurn.seq)
        )
    )


def purge_expired_turns(
    session: Session, *, firm_id: uuid.UUID, actor: Actor, now: datetime | None = None
) -> int:
    """Delete this firm's transcript turns older than its `privacy.transcript_retention_days`.
    Intake records, escalations and audit history are kept; only conversation text is removed."""
    now = resolve_now(now)
    firm = get_active_firm(session, firm_id)
    cutoff = now - timedelta(days=load_config(firm).privacy.transcript_retention_days)
    deleted = len(
        session.scalars(
            delete(TranscriptTurn)
            .where(TranscriptTurn.firm_id == firm_id, TranscriptTurn.created_at < cutoff)
            .returning(TranscriptTurn.id)
        ).all()
    )
    if deleted:
        record_audit(
            session,
            firm_id=firm_id,
            actor=actor,
            action="transcripts.purged",
            entity_type="firm",
            entity_id=firm_id,
            details={"deleted_turns": deleted, "older_than": cutoff.isoformat()},
        )
    session.flush()
    return deleted
