"""Phase 8 repeatable demo (blueprint §16): a normal intake, an urgent escalation, a calendar
failure with safe recovery, and a coordinator working the result in the dashboard (video).

Everything below the model is real: tools, PostgreSQL, Temporal workers, the staff API and the
built Next.js dashboard. The model is the scripted one from the evaluation dataset, so the demo
is free, needs no API quota and is exactly repeatable (a live-model run varies per call).

Writes docs/demo/: DEMO.md (transcripts, tool calls, database state, Temporal history, checks),
dashboard_walkthrough.webm and screenshots.

Usage: .venv\\Scripts\\python -u scripts/record_demo.py
Needs postgres + temporal running, `npm run build` in frontend/, ports 3000/8000 free and no
scripts/temporal_worker.py running.
"""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import func, select
from temporalio.client import Client

from app.agent.conversation import IntakeConversation
from app.config import REPO_ROOT, get_settings
from app.db.session import make_engine, make_session_factory
from app.domain import intake_service, staff_auth_service
from app.domain.firm_config_service import firm_today, get_active_firm
from app.evaluation.runner import EvalTemporal, create_eval_firm, retire_eval_firm
from app.evaluation.scenario import Scenario, load_scenarios
from app.evaluation.scripted_llm import ScenarioLLM
from app.models import (
    Appointment,
    CallSession,
    Escalation,
    Firm,
    IntakeRecord,
    StaffMember,
    StaffNotification,
    WorkflowExecution,
)

OUT = REPO_ROOT / "docs" / "demo"
WEB = "http://127.0.0.1:3000"
COORDINATOR = "sana.kapoor@northstar-legal.example"
SCENARIOS = {
    s.id: s
    for s in load_scenarios(REPO_ROOT / "fixtures" / "scenarios" / "transcript_scenarios.json")
}
SCENES = [
    ("normal", "N01_employment_booking", "1. Normal intake with a booked consultation"),
    (
        "urgent",
        "U03_vacate_by_tomorrow",
        "2. Urgent deadline: configured escalation and reviewer handoff",
    ),
    ("calendar", "C01_calendar_timeout_after_commit", "3. Calendar failure and safe recovery"),
]


def wait(predicate: Any, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise TimeoutError(what)


def converse(scenario: Scenario, sessions: Any, firm_id: uuid.UUID, queue: str) -> dict[str, Any]:
    """One conversation through the production loop; returns the transcript with tool calls."""
    with sessions() as s:
        today = firm_today(get_active_firm(s, firm_id), datetime.now(UTC))
    convo = IntakeConversation(
        llm=ScenarioLLM(scenario.scripted_model, today),
        sessions=sessions,
        firm_id=firm_id,
        durable_booking=True,  # bookings run as Temporal workflows, as in production
        booking_task_queue=queue,
    )
    turns: list[dict[str, Any]] = [{"agent": convo.start()}]
    for line in scenario.caller_turns:
        before = len(convo.trace)
        reply = convo.respond(line)
        tools = []
        for entry in convo.trace[before:]:
            result = json.loads(entry.result)
            tools.append(
                {
                    "tool": entry.tool,
                    "args": json.loads(entry.arguments),
                    "ok": result["ok"],
                    "outcome": _outcome(result),
                }
            )
        turns.append({"caller": line, "tools": tools, "agent": reply})
    convo.end()
    assert convo.call_session_id is not None
    return {"turns": turns, "call_session_id": convo.call_session_id}


def _outcome(result: dict[str, Any]) -> str:
    if not result["ok"]:
        return f"error {result['error']['code']}"
    data = result.get("data") or {}
    for key in ("verified", "intake_reference", "saved", "slots", "status"):
        if key in data:
            value = data[key]
            if key == "slots":
                return f"{len(value)} real slots offered"
            if key == "saved":
                rules = [e["rule_id"] for e in data.get("escalations_for_human_review", [])]
                return f"saved as {value['status']}" + (
                    f"; escalation: {', '.join(rules)}" if rules else ""
                )
            if key == "verified":
                return f"verified booking: {data.get('starts_at')} with {data.get('lawyer')}"
            return f"{key}: {value}"
    return "ok"


def db_state(sessions: Any, call_session_id: uuid.UUID) -> dict[str, Any]:
    with sessions() as s:
        call = s.get_one(CallSession, call_session_id)
        intake = s.get_one(IntakeRecord, call.intake_id)
        fields = {
            k: {"value": e.value, "status": str(e.status)}
            for k, e in intake_service.intake_fields(intake).items()
            if str(e.status) != "MISSING"
        }
        escalations = s.scalars(select(Escalation).where(Escalation.intake_id == intake.id)).all()
        booked = s.scalars(
            select(Appointment).where(
                Appointment.intake_id == intake.id, Appointment.status == "BOOKED"
            )
        ).all()
        entity_ids = [call.id, intake.id, *[e.id for e in escalations]]
        workflows = s.scalars(
            select(WorkflowExecution).where(WorkflowExecution.entity_id.in_(entity_ids))
        ).all()
        return {
            "intake_id": intake.id,
            "reference": intake.reference,
            "intake_status": str(intake.status),
            "session_status": str(call.status),
            "fields": fields,
            "escalations": [
                {
                    "id": e.id,
                    "rule": e.rule_id,
                    "priority": str(e.priority),
                    "status": str(e.status),
                }
                for e in escalations
            ],
            "booked_appointments": [
                {"starts_at": a.starts_at.isoformat(), "slot": str(a.slot_id)} for a in booked
            ],
            "workflows": [
                {
                    "id": w.workflow_id,
                    "type": str(w.workflow_type),
                    "status": str(w.status),
                    "result": w.result,
                }
                for w in workflows
            ],
        }


async def activity_story(client: Client, workflow_id: str) -> list[str]:
    """What the workflow actually did, from Temporal's own event history."""
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    names: dict[int, str] = {}
    story = []
    for event in history.events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            names[event.event_id] = (
                event.activity_task_scheduled_event_attributes.activity_type.name
            )
        elif event.HasField("activity_task_completed_event_attributes"):
            name = names[event.activity_task_completed_event_attributes.scheduled_event_id]
            story.append(f"{name}: completed")
        elif event.HasField("activity_task_failed_event_attributes"):
            attrs = event.activity_task_failed_event_attributes
            failure = attrs.failure.cause if attrs.failure.HasField("cause") else attrs.failure
            story.append(f"{names[attrs.scheduled_event_id]}: FAILED ({failure.message})")
        elif event.HasField("timer_started_event_attributes"):
            seconds = event.timer_started_event_attributes.start_to_fire_timeout.seconds
            story.append(f"timer: wait {seconds} s before the next attempt")
    return story


def start(cmd: list[str], cwd: Path, port: int, log: Path) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(cmd, cwd=cwd, stdout=log.open("wb"), stderr=subprocess.STDOUT)

    def listening() -> bool:
        with socket.socket() as sock:
            return sock.connect_ex(("127.0.0.1", port)) == 0

    wait(listening, 90, f"nothing listening on {port}")
    return process


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        process.terminate()


def pause(page: Page, ms: int = 1500) -> None:
    page.wait_for_timeout(ms)  # so the recording can be followed


def dashboard(slug: str, password: str, scenes: dict[str, dict[str, Any]], shots: list[str]) -> str:
    urgent = scenes["urgent"]["db"]
    calendar = scenes["calendar"]["db"]
    urgent_esc = urgent["escalations"][0]["id"]
    videos = OUT / "_video"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(videos),
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.goto(f"{WEB}/login")
        page.get_by_label("Firm").fill(slug)
        page.get_by_label("Email").fill(COORDINATOR)
        page.get_by_label("Password").fill(password)
        pause(page, 800)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_test_id("signed-in-as")).to_contain_text("Sana Kapoor")
        pause(page)
        page.screenshot(path=str(OUT / "dashboard_overview.png"), full_page=True)
        shots.append("dashboard_overview.png")

        page.get_by_role("link", name="Escalation queue").click()
        card = page.get_by_test_id(f"escalation-{urgent_esc}")
        expect(card).to_contain_text("nobody has accepted it yet")
        pause(page)
        card.get_by_role("button", name="Accept").click()
        expect(card).to_contain_text("Assigned to Sana Kapoor")
        pause(page, 2500)
        page.reload()
        expect(page.get_by_test_id(f"escalation-{urgent_esc}")).to_contain_text(
            "COMPLETED (ACCEPTED)"
        )
        page.screenshot(path=str(OUT / "dashboard_queue_after_accept.png"), full_page=True)
        shots.append("dashboard_queue_after_accept.png")
        pause(page)

        page.goto(f"{WEB}/intakes/{calendar['intake_id']}")
        expect(page.get_by_test_id("appointment")).to_contain_text("BOOKED")
        pause(page)
        page.get_by_role("heading", name="Workflows").scroll_into_view_if_needed()
        pause(page, 2000)
        page.get_by_test_id("timeline").scroll_into_view_if_needed()
        pause(page, 2500)
        page.screenshot(path=str(OUT / "dashboard_calendar_intake.png"), full_page=True)
        shots.append("dashboard_calendar_intake.png")

        page.get_by_role("link", name="Bookings").click()
        expect(page.get_by_role("heading", name="Consultation bookings")).to_be_visible()
        pause(page)
        page.get_by_role("link", name="Workflows").click()
        expect(page.get_by_role("heading", name="Durable workflows")).to_be_visible()
        pause(page, 2000)
        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()
    assert video_path is not None
    target = OUT / "dashboard_walkthrough.webm"
    shutil.move(video_path, target)
    shutil.rmtree(videos, ignore_errors=True)
    return target.name


INTRO = (
    "Everything below the model is the real system: validated tools, PostgreSQL, Temporal "
    "workers, the staff API and the Next.js dashboard. The model is the deterministic scripted "
    "model from the evaluation dataset (`fixtures/scenarios`), so this demo needs no API quota "
    "and replays identically; live-model behaviour is measured separately (Phase 6 live track). "
    "People, firm and numbers are invented."
)
RECOVERY = (
    "The first attempt committed the booking, then timed out. Instead of booking again, the "
    "workflow checked the stored state by idempotency key, found the appointment and confirmed "
    "it: one appointment, confirmed to the caller only after it was re-read from the database."
)


def _db_lines(db: dict[str, Any]) -> list[str]:
    fields = "; ".join(
        f"{k} = {v['value'] or '-'} (`{v['status']}`)" for k, v in db["fields"].items()
    )
    escalations = ", ".join(
        f"{e['rule']} {e['priority']} `{e['status']}`" for e in db["escalations"]
    )
    booked = db["booked_appointments"]
    workflows = "; ".join(
        f"{w['type']} `{w['status']}` {w['result'] or ''}".strip() for w in db["workflows"]
    )
    return [
        "**Database afterwards**",
        "",
        f"- Intake {db['reference']}: `{db['intake_status']}`; session `{db['session_status']}`",
        f"- Fields: {fields}",
        f"- Escalations: {escalations or 'none'}",
        f"- Booked appointments: {len(booked)}"
        + (f" ({booked[0]['starts_at']})" if booked else ""),
        f"- Workflows: {workflows}",
        "",
    ]


def write_markdown(report: dict[str, Any]) -> None:
    passed = sum(report["checks"].values())
    lines = [
        "# Demo: Legal Intake & Case Handoff (fictional data)",
        "",
        f"Recorded {report['recorded_at']} by `scripts/record_demo.py` "
        "(run it again to reproduce).",
        "",
        INTRO,
        "",
        f"**Result: {report['result']}** - {passed}/{len(report['checks'])} checks.",
        "",
    ]
    for key, _, title in SCENES:
        scene = report["scenes"][key]
        lines += [f"## {title}", "", f"Scenario `{scene['scenario']}`. {scene['description']}", ""]
        for turn in scene["turns"]:
            if "caller" in turn:
                lines += [f"> **Caller:** {turn['caller']}", ">"]
                for tool in turn["tools"]:
                    args = ", ".join(f"{k}={v!r}" for k, v in tool["args"].items())
                    lines.append(f"> `{tool['tool']}({args})` → {tool['outcome']}")
                if turn["tools"]:
                    lines.append(">")
            lines += [f"> **Assistant:** {turn['agent']}", ""]
        lines += _db_lines(scene["db"])
        if scene.get("booking_history"):
            lines += [
                "**Temporal history of the booking workflow** "
                "(fault: the calendar times out after the booking committed)",
                "",
                *[f"1. {step}" for step in scene["booking_history"]],
                "",
                RECOVERY,
                "",
            ]
    lines += [
        "## 4. Coordinator review in the dashboard",
        "",
        f"Video: [`{report['video']}`]({report['video']}). Sign in, open the escalation queue, "
        "accept the urgent escalation (the waiting handoff workflow completes through the "
        f"`status_changed` signal, {report['handoff_completed_after_accept_s']} s after the "
        "accept), open the calendar-failure intake (one booking, workflows, action history), "
        "bookings, workflows.",
        "",
        *[f"![{shot}]({shot})" for shot in report["screenshots"]],
        "",
        "## Checks",
        "",
        "| Check | Result |",
        "|---|---|",
        *[f"| {name} | {'PASS' if ok else 'FAIL'} |" for name, ok in report["checks"].items()],
    ]
    (OUT / "DEMO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    firm_id = create_eval_firm(sessions, f"demo-{run_id}")
    password = secrets.token_urlsafe(16)  # throwaway, this run only
    with sessions() as s, s.begin():
        slug = s.get_one(Firm, firm_id).slug
        for staff_id in s.scalars(select(StaffMember.id).where(StaffMember.firm_id == firm_id)):
            staff_auth_service.set_password(
                s, firm_id=firm_id, staff_id=staff_id, password=password
            )

    report: dict[str, Any] = {"recorded_at": run_id, "scenes": {}, "checks": {}, "screenshots": []}
    checks: dict[str, bool] = report["checks"]
    worker = EvalTemporal(settings, sessions, calendar_fault=None, notifier_fault=None)
    faulty = EvalTemporal(
        settings, sessions, calendar_fault="timeout_after_commit:1", notifier_fault=None
    )
    api = web = None
    try:
        for key, scenario_id, _ in SCENES:
            scenario = SCENARIOS[scenario_id]
            queue = faulty.queue if key == "calendar" else worker.queue
            run = converse(scenario, sessions, firm_id, queue)
            report["scenes"][key] = {
                "scenario": scenario_id,
                "description": scenario.description,
                **run,
            }
            print(f"scene {key}: done", flush=True)

        def handoffs_alerted() -> bool:
            worker.dispatch(firm_id)
            with sessions() as s:
                open_ids = set(
                    s.scalars(select(Escalation.id).where(Escalation.firm_id == firm_id))
                )
                alerted = set(
                    s.scalars(
                        select(StaffNotification.escalation_id).where(
                            StaffNotification.firm_id == firm_id,
                            StaffNotification.kind == "NEW_ESCALATION",
                        )
                    )
                )
            return bool(open_ids) and open_ids <= alerted

        wait(handoffs_alerted, 60, "reviewer alerts")
        for key in report["scenes"]:
            report["scenes"][key]["db"] = db_state(
                sessions, report["scenes"][key]["call_session_id"]
            )

        normal, urgent, calendar = (
            report["scenes"][k]["db"] for k in ("normal", "urgent", "calendar")
        )
        checks["normal: one verified booking, intake pending review"] = (
            len(normal["booked_appointments"]) == 1 and normal["intake_status"] == "PENDING_REVIEW"
        )
        checks["urgent: imminent_deadline escalation, reviewer alerted"] = [
            e["rule"] for e in urgent["escalations"]
        ] == ["imminent_deadline"]
        booking_wf = next(w for w in calendar["workflows"] if w["type"] == "BOOKING")
        story = faulty.call(activity_story(faulty.client, booking_wf["id"]))  # type: ignore[arg-type]
        report["scenes"]["calendar"]["booking_history"] = story
        checks["calendar: first attempt failed after commit"] = any(
            s.startswith("book_slot: FAILED") for s in story
        )
        checks["calendar: state checked instead of booking again"] = (
            "find_booking: completed" in story
        )
        checks["calendar: exactly one appointment, workflow BOOKED"] = (
            len(calendar["booked_appointments"]) == 1 and booking_wf["result"] == "BOOKED"
        )
        with sessions() as s:
            checks["two bookings in total across the demo (no duplicates)"] = (
                s.scalar(
                    select(func.count())
                    .select_from(Appointment)
                    .where(Appointment.firm_id == firm_id, Appointment.status == "BOOKED")
                )
                == 2
            )

        env_log = OUT / "_logs"
        env_log.mkdir(exist_ok=True)
        uvicorn = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
        api = start([sys.executable, *uvicorn], REPO_ROOT / "backend", 8000, env_log / "api.log")
        npm = "npm.cmd" if os.name == "nt" else "npm"
        web = start([npm, "run", "start"], REPO_ROOT / "frontend", 3000, env_log / "web.log")

        clicked = time.monotonic()
        report["video"] = dashboard(slug, password, report["scenes"], report["screenshots"])
        urgent_wf = next(w["id"] for w in urgent["workflows"] if w["type"] == "HANDOFF")
        with sessions() as s:
            handoff = s.scalars(
                select(WorkflowExecution).where(WorkflowExecution.workflow_id == urgent_wf)
            ).one()
            checks["dashboard: accept completed the handoff workflow"] = (
                handoff.status == "COMPLETED" and handoff.result == "ACCEPTED"
            )
            assert handoff.completed_at is not None
            accepted = s.scalar(
                select(Escalation.accepted_at).where(
                    Escalation.id == urgent["escalations"][0]["id"]
                )
            )
            assert accepted is not None
            report["handoff_completed_after_accept_s"] = round(
                (handoff.completed_at - accepted).total_seconds(), 2
            )
        report["dashboard_seconds"] = round(time.monotonic() - clicked, 1)
    finally:
        stop(web)
        stop(api)
        with sessions() as s:
            open_wf = s.scalars(
                select(WorkflowExecution.workflow_id).where(
                    WorkflowExecution.firm_id == firm_id,
                    WorkflowExecution.status.in_(["REQUESTED", "STARTED"]),
                )
            ).all()
        worker.terminate_open_workflows(list(open_wf))
        worker.close()
        faulty.close()
        retire_eval_firm(sessions, firm_id)
        shutil.rmtree(OUT / "_logs", ignore_errors=True)

    report["result"] = "PASS" if checks and all(checks.values()) else "FAIL"
    write_markdown(report)
    print(json.dumps({"result": report["result"], "checks": checks}, indent=2))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
