"""Playwright UI tests of the review dashboard (Phase 7): a real Chromium drives the built Next.js
app, which proxies /api to a real FastAPI process on the test database.

Run explicitly (excluded from the default run):
    cd frontend; npm run build; cd ..\\backend
    ..\\.venv\\Scripts\\python -m pytest -m ui
Needs ports 3000 and 8000 free. No LLM, no Temporal (the handoff signal is disabled).
"""

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain import escalation_service, intake_service, notification_service, staff_auth_service
from app.domain.actors import Actor
from app.domain.firm_config_service import firm_today, get_active_firm
from app.models import Escalation
from app.models.enums import FieldStatus, NotificationKind, StaffRole
from tests.integration.conftest import SeededFirm

pytestmark = pytest.mark.ui

REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "frontend"
API_PORT, WEB_PORT = 8000, 3000
WEB = f"http://127.0.0.1:{WEB_PORT}"
PASSWORD = "ui-test password 1"
COORDINATOR = "sana.kapoor@northstar-legal.example"
ADMIN = "rohan.verma@northstar-legal.example"
AGENT = Actor.agent()
Demo = tuple[sessionmaker[Session], SeededFirm]


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for(port: int, process: subprocess.Popen[bytes], timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server on port {port} exited with {process.returncode}")
        if _port_busy(port):
            return
        time.sleep(0.3)
    raise TimeoutError(f"nothing listening on port {port}")


@pytest.fixture(scope="module")
def servers(engine: Engine, test_db_url: str) -> Iterator[None]:
    if not (FRONTEND / ".next" / "BUILD_ID").exists():
        pytest.fail("frontend is not built: run `npm run build` in frontend/")
    busy = [p for p in (API_PORT, WEB_PORT) if _port_busy(p)]
    if busy:
        pytest.fail(f"ports in use: {busy} (stop the dev servers first)")
    env = {**os.environ, "DATABASE_URL": test_db_url, "HANDOFF_SIGNAL_ENABLED": "false"}
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(API_PORT)],
        cwd=REPO / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    npm = "npm.cmd" if os.name == "nt" else "npm"
    web = subprocess.Popen(
        [npm, "run", "start"], cwd=FRONTEND, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        _wait_for(API_PORT, api)
        _wait_for(WEB_PORT, web)
        yield
    finally:
        for process in (web, api):
            if os.name == "nt":  # npm spawns node; kill the whole tree
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True, check=False)  # fmt: skip
            else:
                process.terminate()
            process.wait(timeout=20)


@pytest.fixture(scope="module")
def browser(servers: None) -> Iterator[Browser]:
    with sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(10_000)
    yield page
    context.close()


@pytest.fixture
def firm(committed_demo: Demo) -> Demo:
    sessions, seeded = committed_demo
    with sessions() as s, s.begin():
        for staff_id in seeded.staff_ids.values():
            staff_auth_service.set_password(
                s, firm_id=seeded.firm_id, staff_id=staff_id, password=PASSWORD
            )
    return committed_demo


def seed_urgent(demo: Demo, name: str = "Priya Sharma") -> tuple[uuid.UUID, uuid.UUID, str]:
    sessions, seeded = demo
    with sessions() as s, s.begin():
        today = firm_today(get_active_firm(s, seeded.firm_id), datetime.now(UTC))
        intake = intake_service.create_draft_intake(s, firm_id=seeded.firm_id, actor=AGENT)
        for key, value in (
            ("caller_name", name),
            ("reported_deadline", (today + timedelta(days=1)).isoformat()),
        ):
            intake_service.save_intake_answer(
                s,
                firm_id=seeded.firm_id,
                intake_id=intake.id,
                actor=AGENT,
                field_key=key,
                status=FieldStatus.REPORTED_BY_CALLER,
                value=value,
            )
        [(_, result)] = escalation_service.apply_escalation_rules(
            s, firm_id=seeded.firm_id, intake_id=intake.id, actor=AGENT
        )
        intake_service.finalize_intake(
            s,
            firm_id=seeded.firm_id,
            intake_id=intake.id,
            actor=AGENT,
            summary="Caller reports a notice with a deadline.",
        )
        return intake.id, result.escalation.id, intake.reference


def login(
    page: Page, email: str = COORDINATOR, password: str = PASSWORD, *, expect_ok: bool = True
) -> None:
    page.goto(f"{WEB}/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    if expect_ok:  # the session cookie is set once the signed-in header renders
        expect(page.get_by_test_id("signed-in-as")).to_be_visible()


def app_alert(page: Page):  # type: ignore[no-untyped-def]
    """The page's own error message (Next.js also renders an empty role=alert announcer)."""
    return page.locator("p[role=alert]")


def test_sign_in_rejects_a_wrong_password_then_redirects_back(page: Page, firm: Demo) -> None:
    login(page, password="definitely wrong", expect_ok=False)
    expect(app_alert(page)).to_have_text("Invalid email or password.")

    page.goto(f"{WEB}/escalations")
    expect(page).to_have_url(f"{WEB}/login?next=%2Fescalations")
    page.get_by_label("Email").fill(COORDINATOR)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Escalation queue")).to_be_visible()
    expect(page.get_by_test_id("signed-in-as")).to_contain_text("Sana Kapoor")


def test_coordinator_accepts_then_resolves_an_urgent_escalation(page: Page, firm: Demo) -> None:
    _, escalation_id, reference = seed_urgent(firm)
    login(page)
    expect(page.get_by_test_id("count-pending")).to_have_text("1")
    page.get_by_role("link", name="Escalation queue").click()

    card = page.get_by_test_id(f"escalation-{escalation_id}")
    expect(card).to_contain_text(reference)
    expect(card.get_by_test_id("handoff-meaning")).to_contain_text("nobody has accepted it yet")
    card.get_by_role("button", name="Accept").click()
    expect(card.get_by_test_id("handoff-meaning")).to_contain_text("Assigned to Sana Kapoor")
    expect(card.get_by_role("button", name="Accept")).to_have_count(0)

    resolve = card.get_by_role("button", name="Resolve")
    expect(resolve).to_be_disabled()  # a resolution note is required
    card.get_by_label("Resolution note").fill("Called the client; consultation confirmed.")
    resolve.click()
    expect(card).to_contain_text("Resolution: Called the client; consultation confirmed.")

    sessions, _ = firm
    with sessions() as s:  # the dashboard showed what the database holds
        assert s.get_one(Escalation, escalation_id).status == "RESOLVED"


def test_intake_review_verify_field_close_and_history(page: Page, firm: Demo) -> None:
    intake_id, escalation_id, reference = seed_urgent(firm)
    login(page)
    page.goto(f"{WEB}/intakes/{intake_id}")
    expect(page.get_by_test_id("intake-reference")).to_have_text(reference)

    name = page.get_by_test_id("field-caller_name")
    expect(name).to_contain_text("Reported by caller (unverified)")
    name.get_by_role("button", name="Verify").click()
    expect(name).to_contain_text("Verified by staff")

    page.get_by_role("button", name="Close intake").click()
    expect(app_alert(page)).to_contain_text("resolve the intake's open escalations")

    card = page.get_by_test_id(f"escalation-{escalation_id}")
    card.get_by_label("Resolution note").fill("Handled by phone.")
    card.get_by_role("button", name="Resolve").click()
    expect(card).to_contain_text("Resolution: Handled by phone.")
    page.get_by_role("button", name="Close intake").click()
    expect(page.get_by_role("button", name="Close intake")).to_have_count(0)

    timeline = page.get_by_test_id("timeline")
    for action in ("intake.created", "escalation.created", "intake.field_saved",
                   "escalation.resolved", "intake.status_changed"):  # fmt: skip
        expect(timeline).to_contain_text(action)


def test_administrator_sees_state_but_cannot_accept(page: Page, firm: Demo) -> None:
    _, escalation_id, _ = seed_urgent(firm)
    login(page, ADMIN)
    page.goto(f"{WEB}/escalations")
    card = page.get_by_test_id(f"escalation-{escalation_id}")
    expect(card).to_contain_text("Only lawyers and intake coordinators")
    expect(card.get_by_role("button", name="Accept")).to_have_count(0)


def test_failed_handoff_is_shown_and_can_only_be_resolved(page: Page, firm: Demo) -> None:
    sessions, seeded = firm
    _, escalation_id, _ = seed_urgent(firm)
    with sessions() as s, s.begin():
        escalation_service.mark_escalation_failed(
            s,
            firm_id=seeded.firm_id,
            escalation_id=escalation_id,
            actor=Actor.system(),
            error="Reviewer alert could not be delivered: channel unavailable",
        )
        notification_service.notify(
            s,
            firm_id=seeded.firm_id,
            escalation_id=escalation_id,
            recipient_role=StaffRole.ADMINISTRATOR,
            kind=NotificationKind.HANDOFF_FAILED_FALLBACK,
            dedupe_key=f"handoff:{escalation_id}:failed",
            message="Reviewer alerts could not be delivered for this escalation.",
        )
    login(page)
    expect(page.get_by_test_id("count-failed")).to_have_text("1")
    page.goto(f"{WEB}/escalations?status=FAILED")
    card = page.get_by_test_id(f"escalation-{escalation_id}")
    expect(card.get_by_test_id("handoff-meaning")).to_contain_text("could not be delivered")
    expect(card).to_contain_text("HANDOFF FAILED FALLBACK")
    expect(card).to_contain_text("Not delivered")
    expect(card.get_by_role("button", name="Accept")).to_have_count(0)
    expect(card.get_by_role("button", name="Resolve")).to_have_count(1)
