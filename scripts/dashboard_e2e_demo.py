"""Phase 7 end-to-end evidence: a simulated coordinator works the real review queue.

1. A fresh firm gets real intakes from the conversation pipeline (scripted model, real tools, DB):
   a failed handoff (reviewer channel down, via a faulty Temporal worker), an urgent booked intake,
   an LLM outage and a hang-up. Their handoff workflows run on a live Temporal worker.
2. FastAPI (staff API) and the built Next.js dashboard are started.
3. Chromium signs in as the intake coordinator, checks that the dashboard shows the real handoff
   state, accepts and resolves escalations, closes an intake. Accepting must complete the waiting
   HandoffWorkflow through the status_changed signal (without it, it would wait 15 minutes).

Usage: .venv\\Scripts\\python -u scripts/dashboard_e2e_demo.py [--out docs/evidence/phase7]
Needs postgres + temporal running, `npm run build` in frontend/, ports 3000/8000 free, and no
scripts/temporal_worker.py running.
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select

from app.config import REPO_ROOT, get_settings
from app.db.session import make_engine, make_session_factory
from app.domain import staff_auth_service, workflow_service
from app.evaluation.runner import (
    EvalContext,
    EvalTemporal,
    create_eval_firm,
    retire_eval_firm,
    run_scenario,
)
from app.evaluation.scenario import Scenario, load_scenarios
from app.models import (
    CallSession,
    Escalation,
    Firm,
    StaffMember,
    StaffNotification,
    WorkflowExecution,
)

WEB = "http://127.0.0.1:3000"
COORDINATOR = "sana.kapoor@northstar-legal.example"
SCENARIOS = REPO_ROOT / "fixtures" / "scenarios" / "transcript_scenarios.json"


def without_handoff_wait(scenario: Scenario) -> Scenario:
    """Run the conversation only; its outbox rows are dispatched to the long-lived worker."""
    raw = scenario.model_dump(mode="json", by_alias=True, exclude_none=True)
    raw["expect"].pop("handoff", None)
    return Scenario.model_validate(raw)


def wait(predicate: Any, timeout: float, what: str) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if predicate():
            return time.monotonic() - started
        time.sleep(0.5)
    raise TimeoutError(what)


def start(
    cmd: list[str], cwd: Path, port: int, log: Path, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=log.open("wb"), stderr=subprocess.STDOUT
    )

    def listening() -> bool:
        with socket.socket() as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    wait(listening, 90, f"nothing listening on {port}")
    return process


def stop(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        process.terminate()


def shot(page: Page, out: Path, name: str) -> str:
    path = out / f"{name}.png"
    page.screenshot(path=str(path), full_page=True)
    return path.name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "evidence" / "phase7")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {"run_id": run_id, "checks": {}, "screenshots": []}
    checks: dict[str, bool] = report["checks"]

    # --- 1. real intakes --------------------------------------------------------------------
    firm_id = create_eval_firm(sessions, f"dash-{run_id}")
    password = secrets.token_urlsafe(16)  # throwaway, this run only
    with sessions() as s, s.begin():
        slug = s.get_one(Firm, firm_id).slug
        for staff_id in s.scalars(select(StaffMember.id).where(StaffMember.firm_id == firm_id)):
            staff_auth_service.set_password(
                s, firm_id=firm_id, staff_id=staff_id, password=password
            )
    by_id = {s.id: s for s in load_scenarios(SCENARIOS)}
    ctx = EvalContext(settings, sessions, firm_id)

    failed = run_scenario(by_id["H01_reviewer_channel_down"], "system", ctx)  # own faulty worker
    report["seed"] = {"H01_reviewer_channel_down": failed.status}
    worker = EvalTemporal(settings, sessions, calendar_fault=None, notifier_fault=None)
    api = web = None
    try:
        for sid in (
            "U01_respond_by_tomorrow_booking",
            "F01_llm_outage_mid_call",
            "X01_hangup_mid_intake",
        ):
            report["seed"][sid] = run_scenario(
                without_handoff_wait(by_id[sid]), "system", ctx
            ).status
        print("seeded:", report["seed"], flush=True)

        with sessions() as s:
            open_ids = s.scalars(
                select(Escalation.id).where(
                    Escalation.firm_id == firm_id, Escalation.status == "PENDING"
                )
            ).all()

        def alerted() -> bool:
            worker.dispatch(firm_id)
            with sessions() as s:
                kinds = s.scalars(
                    select(StaffNotification.escalation_id).where(
                        StaffNotification.firm_id == firm_id,
                        StaffNotification.kind == "NEW_ESCALATION",
                    )
                ).all()
            return set(open_ids) <= set(kinds)

        wait(alerted, 60, "reviewer alerts for the pending escalations")
        with sessions() as s:
            urgent = s.scalars(
                select(Escalation).where(
                    Escalation.firm_id == firm_id,
                    Escalation.status == "PENDING",
                    Escalation.rule_id == "imminent_deadline",
                )
            ).one()
            failed_esc = s.scalars(
                select(Escalation).where(
                    Escalation.firm_id == firm_id, Escalation.status == "FAILED"
                )
            ).one()
            urgent_wf = workflow_service.handoff_workflow_id(urgent.id)
            checks["urgent_handoff_running_before_accept"] = (
                s.scalars(
                    select(WorkflowExecution.status).where(
                        WorkflowExecution.workflow_id == urgent_wf
                    )
                ).one()
                == "STARTED"
            )

        # --- 2. servers -------------------------------------------------------------------
        env = {**os.environ, "HANDOFF_SIGNAL_ENABLED": "true"}
        uvicorn = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
        api = start(
            [sys.executable, *uvicorn], REPO_ROOT / "backend", 8000, args.out / "api.log", env
        )
        web = start(
            ["npm.cmd" if os.name == "nt" else "npm", "run", "start"],
            REPO_ROOT / "frontend", 3000, args.out / "web.log", env,
        )  # fmt: skip

        # --- 3. the coordinator -----------------------------------------------------------
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(15_000)
            page.goto(f"{WEB}/login")
            page.get_by_label("Firm").fill(slug)
            page.get_by_label("Email").fill(COORDINATOR)
            page.get_by_label("Password").fill(password)
            page.get_by_role("button", name="Sign in").click()
            expect(page.get_by_test_id("signed-in-as")).to_contain_text("Sana Kapoor")
            expect(page.get_by_test_id("count-failed")).to_have_text("1")
            checks["overview_counts_match_db"] = page.get_by_test_id(
                "count-pending"
            ).inner_text() == str(len(open_ids))
            report["screenshots"].append(shot(page, args.out, "01_overview"))

            page.goto(f"{WEB}/escalations")
            failed_card = page.get_by_test_id(f"escalation-{failed_esc.id}")
            expect(failed_card).to_contain_text("could not be delivered")
            checks["failed_handoff_shown_without_accept"] = (
                failed_card.get_by_role("button", name="Accept").count() == 0
                and "HANDOFF FAILED FALLBACK" in failed_card.inner_text()
            )
            card = page.get_by_test_id(f"escalation-{urgent.id}")
            expect(card).to_contain_text("nobody has accepted it yet")
            checks["urgent_alert_shown_as_delivered"] = "Delivered" in card.inner_text()
            report["screenshots"].append(shot(page, args.out, "02_queue_before_accept"))

            card.get_by_role("button", name="Accept").click()
            accepted_at = time.monotonic()
            expect(card).to_contain_text("Assigned to Sana Kapoor")

            def workflow_done() -> bool:
                with sessions() as s:
                    w = s.scalars(
                        select(WorkflowExecution).where(WorkflowExecution.workflow_id == urgent_wf)
                    ).one()
                    return w.status == "COMPLETED" and w.result == "ACCEPTED"

            wait(workflow_done, 60, "handoff workflow to finish after the accept signal")
            report["handoff_completed_after_accept_s"] = round(time.monotonic() - accepted_at, 2)
            checks["accept_signal_completed_handoff_workflow"] = True
            page.reload()
            expect(page.get_by_test_id(f"escalation-{urgent.id}")).to_contain_text(
                "COMPLETED (ACCEPTED)"
            )
            checks["dashboard_shows_completed_workflow"] = True
            report["screenshots"].append(shot(page, args.out, "03_queue_after_accept"))

            failed_card = page.get_by_test_id(f"escalation-{failed_esc.id}")
            failed_card.get_by_label("Resolution note").fill(
                "Reviewer channel was down; assigned manually by phone."
            )
            failed_card.get_by_role("button", name="Resolve").click()
            expect(failed_card).to_contain_text("Resolution:")

            page.goto(f"{WEB}/intakes/{urgent.intake_id}")
            expect(page.get_by_test_id("appointment")).to_contain_text("BOOKED")
            checks["booked_consultation_shown"] = True
            name_row = page.get_by_test_id("field-caller_name")
            name_row.get_by_role("button", name="Verify").click()
            expect(name_row).to_contain_text("Verified by staff")
            report["screenshots"].append(shot(page, args.out, "04_intake_detail"))
            ucard = page.get_by_test_id(f"escalation-{urgent.id}")
            ucard.get_by_label("Resolution note").fill("Spoke to the caller; consultation stands.")
            ucard.get_by_role("button", name="Resolve").click()
            expect(ucard).to_contain_text("Resolution:")
            page.get_by_role("button", name="Close intake").click()
            expect(page.get_by_role("button", name="Close intake")).to_have_count(0)
            checks["intake_closed_after_resolution"] = True
            timeline = page.get_by_test_id("timeline").inner_text()
            expected_actions = (
                "intake.created",
                "appointment.booked",
                "escalation.accepted",
                "escalation.resolved",
                "intake.status_changed",
            )
            checks["timeline_has_agent_and_staff_actions"] = all(
                a in timeline for a in expected_actions
            )
            report["screenshots"].append(shot(page, args.out, "05_intake_closed_timeline"))

            page.goto(f"{WEB}/workflows")
            expect(page.get_by_role("heading", name="Durable workflows")).to_be_visible()
            report["screenshots"].append(shot(page, args.out, "06_workflows"))
            browser.close()

        with sessions() as s:
            report["db_after"] = {
                "escalations": [
                    {"reason": str(e.reason_code), "rule": e.rule_id, "status": str(e.status)}
                    for e in s.scalars(select(Escalation).where(Escalation.firm_id == firm_id))
                ],
                "workflows": [
                    {"type": str(w.workflow_type), "status": str(w.status), "result": w.result}
                    for w in s.scalars(
                        select(WorkflowExecution).where(WorkflowExecution.firm_id == firm_id)
                    )
                ],
                "sessions": [
                    str(c.status)
                    for c in s.scalars(select(CallSession).where(CallSession.firm_id == firm_id))
                ],
            }
    finally:
        for process in (web, api):
            if process is not None:
                stop(process)
        with sessions() as s:
            open_wf = s.scalars(
                select(WorkflowExecution.workflow_id).where(
                    WorkflowExecution.firm_id == firm_id,
                    WorkflowExecution.status.in_(["REQUESTED", "STARTED"]),
                )
            ).all()
        worker.terminate_open_workflows(list(open_wf))  # e.g. post-call timers of the hang-up
        worker.close()
        retire_eval_firm(sessions, firm_id)

    passed = bool(checks) and all(checks.values())
    report["result"] = "PASS" if passed else "FAIL"
    (args.out / "dashboard_e2e.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
