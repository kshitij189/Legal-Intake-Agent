import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.conversation import INTERRUPTED_NOTE, IntakeConversation
from app.domain import transcript_service
from app.models import CallSession, Firm, TranscriptTurn
from app.models.enums import Speaker
from tests.fakes import ScriptedLLM, say
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm

Demo = tuple[sessionmaker[Session], SeededFirm]


def start(demo: Demo, *replies: str) -> IntakeConversation:
    sessions, firm = demo
    convo = IntakeConversation(
        llm=ScriptedLLM([say(r) for r in replies]),
        sessions=sessions,
        firm_id=firm.firm_id,
        clock=lambda: NOW,
    )
    convo.start()
    return convo


def turns(demo: Demo) -> list[TranscriptTurn]:
    with demo[0]() as s:
        return list(s.scalars(select(TranscriptTurn).order_by(TranscriptTurn.seq)).all())


def test_turns_are_recorded_with_disclosure_and_consent(committed_demo: Demo) -> None:
    convo = start(committed_demo, "Thanks. What is your name?")
    convo.respond("Hello, I need help with my salary.")

    recorded = turns(committed_demo)
    assert [(t.seq, t.speaker) for t in recorded] == [
        (1, Speaker.AGENT),
        (2, Speaker.CALLER),
        (3, Speaker.AGENT),
    ]
    assert "transcribed" in recorded[0].text  # the greeting discloses retention
    assert recorded[1].text == "Hello, I need help with my salary."
    assert recorded[2].processing_ms is not None and recorded[2].processing_ms >= 0
    assert convo.last_processing_ms == recorded[2].processing_ms
    with committed_demo[0]() as s:
        consent = s.scalars(select(CallSession.consent)).one()
    assert consent["transcript_retained"] is True
    assert consent["audio_recorded"] is False
    assert consent["disclosure"] in recorded[0].text


def test_nothing_is_retained_when_the_firm_opts_out(committed_demo: Demo) -> None:
    sessions, firm = committed_demo
    with sessions() as s, s.begin():
        record = s.get_one(Firm, firm.firm_id)
        record.config = {**record.config, "privacy": {"retain_transcripts": False}}
    convo = start(committed_demo, "Okay.")
    convo.respond("Hello")
    assert turns(committed_demo) == []
    with sessions() as s:
        consent = s.scalars(select(CallSession.consent)).one()
    assert consent == {"transcript_retained": False, "disclosure": None, "audio_recorded": False}
    assert "transcribed" not in convo._messages[1]["content"]  # no disclosure needed


def test_interrupted_reply_is_flagged_in_transcript_and_history(committed_demo: Demo) -> None:
    convo = start(committed_demo, "A long answer the caller talks over.")
    convo.respond("Hi")
    convo.mark_last_reply_interrupted()
    last = turns(committed_demo)[-1]
    assert (last.speaker, last.interrupted) == (Speaker.AGENT, True)
    assert convo._messages[-1]["content"].endswith(INTERRUPTED_NOTE)


def test_transcripts_are_tenant_scoped(committed_demo: Demo) -> None:
    convo = start(committed_demo, "Hello there.")
    convo.respond("Hi")
    assert convo.call_session_id is not None
    sessions, firm = committed_demo
    with sessions() as s:
        assert (
            len(
                transcript_service.list_turns(
                    s, firm_id=firm.firm_id, call_session_id=convo.call_session_id
                )
            )
            == 3
        )
        # Another tenant's id sees nothing, even with a valid session id.
        assert (
            transcript_service.list_turns(
                s, firm_id=uuid.uuid4(), call_session_id=convo.call_session_id
            )
            == []
        )


def test_retention_purges_only_expired_turns_of_that_firm(committed_demo: Demo) -> None:
    from datetime import timedelta

    from app.domain.actors import Actor
    from app.models import AuditEvent, IntakeRecord

    sessions, firm = committed_demo
    convo = start(committed_demo, "Thank you.")
    convo.respond("Hello")
    with sessions() as s, s.begin():
        stored = s.scalars(select(TranscriptTurn)).all()
        assert len(stored) == 3
        stored[0].created_at = NOW - timedelta(days=91)  # older than the 90-day default
    with sessions() as s, s.begin():
        deleted = transcript_service.purge_expired_turns(
            s, firm_id=firm.firm_id, actor=Actor.system("retention"), now=NOW
        )
    assert deleted == 1
    assert len(turns(committed_demo)) == 2
    with sessions() as s:
        event = s.scalars(select(AuditEvent).where(AuditEvent.action == "transcripts.purged")).one()
        assert event.details["deleted_turns"] == 1  # counts only, never transcript text
        assert s.scalars(select(IntakeRecord)).all() == []  # nothing else touched
    with sessions() as s, s.begin():  # idempotent
        assert (
            transcript_service.purge_expired_turns(
                s, firm_id=firm.firm_id, actor=Actor.system("retention"), now=NOW
            )
            == 0
        )
