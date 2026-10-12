"""Outbox dispatch scope (Phase 8): requests of deactivated firms are never started."""

from sqlalchemy.orm import Session

from app.domain import escalation_service, intake_service, workflow_service
from app.domain.actors import Actor
from app.models import Firm
from app.models.enums import EscalationReason
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm

AGENT = Actor.agent()


def test_dispatcher_skips_requests_of_inactive_firms(session: Session, demo: SeededFirm) -> None:
    intake = intake_service.create_draft_intake(session, firm_id=demo.firm_id, actor=AGENT, now=NOW)
    escalation_service.create_escalation(
        session,
        firm_id=demo.firm_id,
        actor=AGENT,
        intake_id=intake.id,
        reason_code=EscalationReason.UNABLE_TO_COMPLETE,
        description="Evaluation run leftover.",
        idempotency_key="unable:outbox-test",
        now=NOW,
    )
    assert [e.workflow_type for e in workflow_service.claim_requested(session)] == ["HANDOFF"]

    session.get_one(Firm, demo.firm_id).is_active = False  # e.g. a retired evaluation firm
    session.flush()
    assert workflow_service.claim_requested(session) == []
    assert workflow_service.claim_requested(session, firm_ids=[demo.firm_id]) == []
