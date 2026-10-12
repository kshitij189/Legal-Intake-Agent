"""Staff API (Phase 7) against the real test database: authentication, tenant isolation, the
review actions and their effect on persisted state, audit and the handoff signal."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import SESSION_COOKIE
from app.domain import escalation_service, intake_service, staff_auth_service
from app.domain.actors import Actor
from app.domain.firm_config_service import firm_today, get_active_firm
from app.main import create_app
from app.models import AuditEvent, Escalation, IntakeRecord
from app.models.enums import EscalationReason, FieldStatus, IntakeStatus
from tests.integration.conftest import SeededFirm

PASSWORD = "correct horse battery"
AGENT = Actor.agent()
COORDINATOR = "sana.kapoor@northstar-legal.example"
ADMIN = "rohan.verma@northstar-legal.example"
Demo = tuple[sessionmaker[Session], SeededFirm]


@dataclass
class Api:
    client: TestClient
    sessions: sessionmaker[Session]
    firm: SeededFirm
    signals: list[uuid.UUID] = field(default_factory=list)

    def login(self, email: str = COORDINATOR, password: str = PASSWORD) -> None:
        response = self.client.post(
            "/api/v1/auth/login",
            json={"firm": "northstar-legal", "email": email, "password": password},
        )
        assert response.status_code == 200, response.text


@pytest.fixture
def api(committed_demo: Demo) -> Iterator[Api]:
    sessions, firm = committed_demo
    with sessions() as s, s.begin():
        for staff_id in firm.staff_ids.values():
            staff_auth_service.set_password(
                s, firm_id=firm.firm_id, staff_id=staff_id, password=PASSWORD
            )
    signals: list[uuid.UUID] = []
    app = create_app(sessions=sessions, handoff_signal=signals.append)
    with TestClient(app) as client:
        yield Api(client, sessions, firm, signals)


def urgent_intake(api: Api, *, finalize: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """An agent-created intake with a deadline tomorrow -> imminent_deadline escalation."""
    with api.sessions() as s, s.begin():
        now = datetime.now(UTC)
        today = firm_today(get_active_firm(s, api.firm.firm_id), now)
        intake = intake_service.create_draft_intake(s, firm_id=api.firm.firm_id, actor=AGENT)
        for key, value in (
            ("caller_name", "Priya Sharma"),
            ("reported_deadline", (today + timedelta(days=1)).isoformat()),
        ):
            intake_service.save_intake_answer(
                s,
                firm_id=api.firm.firm_id,
                intake_id=intake.id,
                actor=AGENT,
                field_key=key,
                status=FieldStatus.REPORTED_BY_CALLER,
                value=value,
            )
        [(_, result)] = escalation_service.apply_escalation_rules(
            s, firm_id=api.firm.firm_id, intake_id=intake.id, actor=AGENT
        )
        if finalize:
            intake_service.finalize_intake(
                s,
                firm_id=api.firm.firm_id,
                intake_id=intake.id,
                actor=AGENT,
                summary="Caller reports a notice with a deadline.",
            )
        return intake.id, result.escalation.id


# --- authentication -------------------------------------------------------------------------


def test_requests_without_a_session_are_rejected(api: Api) -> None:
    for path in ("/api/v1/intakes", "/api/v1/escalations", "/api/v1/auth/me"):
        response = api.client.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"


def test_login_failures_are_indistinguishable(api: Api) -> None:
    bodies = [
        {"firm": "northstar-legal", "email": COORDINATOR, "password": "wrong password"},
        {"firm": "northstar-legal", "email": "nobody@example.com", "password": PASSWORD},
        {"firm": "other-firm", "email": COORDINATOR, "password": PASSWORD},
    ]
    responses = [api.client.post("/api/v1/auth/login", json=b) for b in bodies]
    assert {r.status_code for r in responses} == {401}
    assert len({r.text for r in responses}) == 1


def test_login_sets_an_httponly_cookie_and_logout_revokes_it(api: Api) -> None:
    response = api.client.post(
        "/api/v1/auth/login",
        json={"firm": "northstar-legal", "email": COORDINATOR.upper(), "password": PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["can_review"] is True
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    token = api.client.cookies[SESSION_COOKIE]
    assert api.client.get("/api/v1/auth/me").json()["role"] == "INTAKE_COORDINATOR"

    assert api.client.post("/api/v1/auth/logout").status_code == 204
    api.client.cookies.clear()
    revoked = api.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 401


def test_bearer_token_and_session_expiry(api: Api) -> None:
    with api.sessions() as s, s.begin():
        token, _ = staff_auth_service.login(
            s, firm_slug="northstar-legal", email=COORDINATOR, password=PASSWORD
        )
    headers = {"Authorization": f"Bearer {token}"}
    assert api.client.get("/api/v1/auth/me", headers=headers).status_code == 200
    with api.sessions() as s:
        later = datetime.now(UTC) + staff_auth_service.SESSION_TTL + timedelta(minutes=1)
        with pytest.raises(staff_auth_service.AuthenticationFailed):
            staff_auth_service.authenticate(s, token, now=later)


def test_password_hashes_are_salted_and_verified() -> None:
    first = staff_auth_service.hash_password(PASSWORD)
    assert first != staff_auth_service.hash_password(PASSWORD)
    assert staff_auth_service.verify_password(PASSWORD, first)
    assert not staff_auth_service.verify_password("wrong password", first)
    assert not staff_auth_service.verify_password(PASSWORD, "garbage")
    with pytest.raises(Exception, match="at least"):
        staff_auth_service.hash_password("short")


# --- tenant isolation -----------------------------------------------------------------------


def test_another_firms_records_are_not_found(api: Api) -> None:
    from app.db.seed import seed_firm

    with api.sessions() as s, s.begin():
        other = seed_firm(s, slug="other-firm", name="Other Firm (fictional)")
        foreign = intake_service.create_draft_intake(s, firm_id=other, actor=AGENT)
    api.login()
    assert api.client.get(f"/api/v1/intakes/{foreign.id}").status_code == 404
    assert foreign.id not in {i["id"] for i in api.client.get("/api/v1/intakes").json()}


# --- review actions -------------------------------------------------------------------------


def test_queue_detail_accept_and_resolve(api: Api) -> None:
    intake_id, escalation_id = urgent_intake(api)
    api.login()

    [queued] = api.client.get("/api/v1/escalations").json()
    assert (queued["id"], queued["status"], queued["priority"]) == (
        str(escalation_id),
        "PENDING",
        "URGENT",
    )
    assert queued["handoff_workflow"]["status"] == "REQUESTED"  # the real outbox row

    accepted = api.client.post(f"/api/v1/escalations/{escalation_id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["assigned_to"] == "Sana Kapoor"
    assert api.signals == [escalation_id]

    again = api.client.post(f"/api/v1/escalations/{escalation_id}/accept")
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_TRANSITION"

    resolved = api.client.post(
        f"/api/v1/escalations/{escalation_id}/resolve", json={"note": "Called the client back."}
    )
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["resolution_note"] == "Called the client back."
    assert api.signals == [escalation_id, escalation_id]

    detail = api.client.get(f"/api/v1/intakes/{intake_id}").json()
    actions = [(e["actor_type"], e["action"]) for e in detail["timeline"]]
    assert actions[0] == ("AGENT", "intake.created")
    assert ("AGENT", "escalation.created") in actions and ("AGENT", "intake.finalized") in actions
    assert actions[-2:] == [("STAFF", "escalation.accepted"), ("STAFF", "escalation.resolved")]
    assert [e["seq"] for e in detail["timeline"]] == sorted(e["seq"] for e in detail["timeline"])


def test_non_reviewer_cannot_accept_or_verify(api: Api) -> None:
    intake_id, escalation_id = urgent_intake(api)
    api.login(ADMIN)
    assert api.client.get("/api/v1/auth/me").json()["can_review"] is False
    response = api.client.post(f"/api/v1/escalations/{escalation_id}/accept")
    assert response.status_code == 403
    verify = api.client.patch(
        f"/api/v1/intakes/{intake_id}/fields/caller_name",
        json={"status": "VERIFIED_BY_SYSTEM", "value": "Priya Sharma"},
    )
    assert verify.status_code == 403
    assert api.signals == []
    with api.sessions() as s:
        assert s.get_one(Escalation, escalation_id).status == "PENDING"


def test_failed_handoff_cannot_be_accepted_only_resolved(api: Api) -> None:
    _, escalation_id = urgent_intake(api)
    with api.sessions() as s, s.begin():
        escalation_service.mark_escalation_failed(
            s,
            firm_id=api.firm.firm_id,
            escalation_id=escalation_id,
            actor=Actor.system(),
            error="Reviewer alert could not be delivered",
        )
    api.login()
    [queued] = api.client.get("/api/v1/escalations").json()
    assert queued["status"] == "FAILED" and queued["last_error"]
    assert api.client.post(f"/api/v1/escalations/{escalation_id}/accept").status_code == 409
    resolve = api.client.post(
        f"/api/v1/escalations/{escalation_id}/resolve", json={"note": "Assigned manually."}
    )
    assert resolve.json()["status"] == "RESOLVED"


def test_reviewer_verifies_and_corrects_fields_rules_reapplied(api: Api) -> None:
    with api.sessions() as s, s.begin():
        intake = intake_service.create_draft_intake(s, firm_id=api.firm.firm_id, actor=AGENT)
        intake_service.save_intake_answer(
            s,
            firm_id=api.firm.firm_id,
            intake_id=intake.id,
            actor=AGENT,
            field_key="reported_deadline",
            status=FieldStatus.PENDING_HUMAN_REVIEW,
            note="Caller unsure: end of next month?",
        )
        today = firm_today(get_active_firm(s, api.firm.firm_id), datetime.now(UTC))
    api.login()

    corrected = api.client.patch(
        f"/api/v1/intakes/{intake.id}/fields/reported_deadline",
        json={"status": "VERIFIED_BY_SYSTEM", "value": (today + timedelta(days=2)).isoformat()},
    ).json()
    deadline = next(f for f in corrected["fields"] if f["key"] == "reported_deadline")
    assert (deadline["status"], deadline["source"]) == ("VERIFIED_BY_SYSTEM", "STAFF")
    assert deadline["history"][0]["status"] == "PENDING_HUMAN_REVIEW"
    # The verified date is now within 3 days: the firm's rule fires, as it would for the agent.
    assert [(e["rule_id"], e["status"]) for e in corrected["escalations"]] == [
        ("imminent_deadline", "PENDING")
    ]
    assert (
        api.client.patch(
            f"/api/v1/intakes/{intake.id}/fields/reported_deadline", json={"status": "MISSING"}
        ).status_code
        == 422
    )
    assert (
        api.client.patch(
            f"/api/v1/intakes/{intake.id}/fields/not_a_field",
            json={"status": "REPORTED_BY_CALLER", "value": "x"},
        ).status_code
        == 422
    )


def test_closing_requires_resolved_escalations(api: Api) -> None:
    intake_id, escalation_id = urgent_intake(api)
    api.login()
    blocked = api.client.post(f"/api/v1/intakes/{intake_id}/close")
    assert blocked.status_code == 409
    api.client.post(f"/api/v1/escalations/{escalation_id}/resolve", json={"note": "Done."})
    closed = api.client.post(f"/api/v1/intakes/{intake_id}/close")
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    with api.sessions() as s:
        assert s.get_one(IntakeRecord, intake_id).status == IntakeStatus.CLOSED
        events = s.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == intake_id)).all()
        assert "intake.status_changed" in events


def test_an_in_progress_intake_cannot_be_closed(api: Api) -> None:
    intake_id, escalation_id = urgent_intake(api, finalize=False)
    api.login()
    api.client.post(f"/api/v1/escalations/{escalation_id}/resolve", json={"note": "Done."})
    assert api.client.post(f"/api/v1/intakes/{intake_id}/close").status_code == 409


def test_lists_notifications_and_workflows(api: Api) -> None:
    from app.domain import notification_service
    from app.models.enums import NotificationKind, StaffRole

    intake_id, escalation_id = urgent_intake(api)
    with api.sessions() as s, s.begin():
        notification, _ = notification_service.notify(
            s,
            firm_id=api.firm.firm_id,
            escalation_id=escalation_id,
            recipient_role=StaffRole.INTAKE_COORDINATOR,
            kind=NotificationKind.NEW_ESCALATION,
            dedupe_key=f"handoff:{escalation_id}:new",
            message="New URGENT escalation",
        )
    api.login()
    [listed] = api.client.get("/api/v1/notifications").json()
    assert listed["intake_reference"].startswith("INT-") and listed["read_at"] is None
    read = api.client.post(f"/api/v1/notifications/{notification.id}/read").json()
    assert read["read_at"] is not None

    workflows = api.client.get("/api/v1/workflows").json()
    assert [w["workflow_type"] for w in workflows] == ["HANDOFF"]
    intakes = api.client.get("/api/v1/intakes", params={"status": "PENDING_REVIEW"}).json()
    assert [(i["id"], i["caller_name"], i["open_escalations"]) for i in intakes] == [
        (str(intake_id), "Priya Sharma", 1)
    ]
    assert api.client.get("/api/v1/intakes", params={"status": "CLOSED"}).json() == []


def test_unable_to_complete_escalation_shows_reason(api: Api) -> None:
    with api.sessions() as s, s.begin():
        intake = intake_service.create_draft_intake(s, firm_id=api.firm.firm_id, actor=AGENT)
        escalation_service.create_escalation(
            s,
            firm_id=api.firm.firm_id,
            actor=AGENT,
            intake_id=intake.id,
            reason_code=EscalationReason.UNABLE_TO_COMPLETE,
            description="Language model unavailable.",
            idempotency_key=f"unable:{uuid.uuid4()}",
        )
    api.login()
    [queued] = api.client.get("/api/v1/escalations").json()
    assert (queued["reason_code"], queued["priority"]) == ("UNABLE_TO_COMPLETE", "HIGH")
    assert "Language model unavailable" in queued["reason_text"]
