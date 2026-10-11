"""Phase 5 acceptance: worker restarts and a Temporal outage must not corrupt records or duplicate
side effects. Runs against the real Temporal server (docker compose) and the main database.

Scenarios:
  booking  - the worker commits a booking, then hangs and is KILLED; a new worker must finish the
             workflow by checking state (no second appointment).
  handoff  - the worker is KILLED while a handoff waits for acknowledgement; after restart the
             reminder and fallback each happen exactly once.
  outbox   - Temporal is STOPPED; an escalation is still created and its handoff waits in the
             outbox; once Temporal is back the dispatcher starts it.

Usage: .venv\\Scripts\\python -u scripts/workflow_restart_test.py [--report FILE]
Takes ~4 minutes (real timers). Writes fictional records to the demo firm.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from temporalio.client import Client

from app.config import REPO_ROOT, get_settings
from app.db.session import make_engine, make_session_factory
from app.domain import appointment_service, escalation_service, intake_service
from app.domain.actors import Actor
from app.domain.workflow_service import booking_workflow_id
from app.models import Appointment, Firm, StaffNotification, WorkflowExecution
from app.models.enums import EscalationReason, FieldStatus
from app.workflows.types import BookingInput
from app.workflows.workflows import BookingWorkflow

SETTINGS = get_settings()
SESSIONS = make_session_factory(make_engine(SETTINGS.database_url))
AGENT = Actor.agent()
QUEUE = f"restart-test-{uuid.uuid4().hex[:8]}"


def start_worker(name: str, **env: str) -> subprocess.Popen[bytes]:
    log = (REPO_ROOT / f"restart_{name}.log").open("wb")
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "temporal_worker.py")],
        cwd=REPO_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "TEMPORAL_TASK_QUEUE": QUEUE, **env},
    )


def kill(proc: subprocess.Popen[bytes]) -> None:
    proc.kill()  # hard kill: no graceful shutdown, like a crashed worker
    proc.wait(timeout=30)


def wait_for(predicate: Any, timeout: float, what: str, interval: float = 0.5) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {what}")


def firm_id() -> uuid.UUID:
    with SESSIONS() as s:
        return s.scalars(select(Firm.id).where(Firm.slug == SETTINGS.demo_firm_slug)).one()


def new_intake(fid: uuid.UUID) -> uuid.UUID:
    with SESSIONS() as s, s.begin():
        intake = intake_service.create_draft_intake(s, firm_id=fid, actor=AGENT)
        intake_service.save_intake_answer(
            s,
            firm_id=fid,
            intake_id=intake.id,
            actor=AGENT,
            field_key="matter_category",
            status=FieldStatus.REPORTED_BY_CALLER,
            value="EMPLOYMENT",
        )
        return intake.id


def escalate(fid: uuid.UUID) -> uuid.UUID:
    with SESSIONS() as s, s.begin():
        return escalation_service.create_escalation(
            s,
            firm_id=fid,
            actor=AGENT,
            intake_id=new_intake(fid),
            reason_code=EscalationReason.UNABLE_TO_COMPLETE,
            description="Restart test: caller could not be understood.",
            idempotency_key=f"restart-{uuid.uuid4().hex}",
        ).escalation.id


def notifications(escalation_id: uuid.UUID) -> list[str]:
    with SESSIONS() as s:
        return [
            str(n.kind)
            for n in s.scalars(
                select(StaffNotification)
                .where(StaffNotification.escalation_id == escalation_id)
                .order_by(StaffNotification.created_at)
            )
        ]


def execution(workflow_id: str) -> WorkflowExecution | None:
    with SESSIONS() as s:
        return s.scalars(
            select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id)
        ).first()


def set_handoff_timing(fid: uuid.UUID, ack_minutes: int, max_reminders: int) -> dict[str, Any]:
    with SESSIONS() as s, s.begin():
        firm = s.get_one(Firm, fid)
        original = dict(firm.config)
        firm.config = {
            **firm.config,
            "handoff": {
                **firm.config.get("handoff", {}),
                "ack_minutes_high": ack_minutes,
                "max_reminders": max_reminders,
            },
        }
        return original


def restore_config(fid: uuid.UUID, config: dict[str, Any]) -> None:
    with SESSIONS() as s, s.begin():
        s.get_one(Firm, fid).config = config


async def booking_scenario(fid: uuid.UUID) -> dict[str, Any]:
    intake_id = new_intake(fid)
    with SESSIONS() as s:
        slot = appointment_service.check_availability(s, firm_id=fid, matter_category="EMPLOYMENT")[
            0
        ]
    key = f"restart-book-{uuid.uuid4().hex[:12]}"
    worker = start_worker("booking_1", CALENDAR_FAULT_INJECTION="hang_after_commit:1")
    client = await Client.connect(SETTINGS.temporal_address)
    handle = await client.start_workflow(
        BookingWorkflow.run,
        BookingInput(str(fid), str(intake_id), str(slot.id), key, "AGENT", "intake-agent"),
        id=booking_workflow_id(key),
        task_queue=QUEUE,
    )

    def committed() -> bool:
        with SESSIONS() as s:
            return (
                appointment_service.find_appointment_by_key(s, firm_id=fid, idempotency_key=key)
                is not None
            )

    await asyncio.to_thread(wait_for, committed, 120, "the booking to commit")
    kill(worker)  # crash after the commit, before the activity reported success
    killed_at = time.monotonic()
    worker = start_worker("booking_2")
    try:
        outcome = await asyncio.wait_for(handle.result(), timeout=120)
    finally:
        kill(worker)
    with SESSIONS() as s:
        by_key = s.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.idempotency_key == key)
        )
        by_intake = s.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.intake_id == intake_id)
        )
    status = execution(booking_workflow_id(key))
    return {
        "outcome": outcome.__dict__ if hasattr(outcome, "__dict__") else outcome,
        "recovered_after_kill_s": round(time.monotonic() - killed_at, 1),
        "appointments_for_key": by_key,
        "appointments_for_intake": by_intake,
        "execution_status": str(status.status) if status else None,
        "passed": (
            getattr(outcome, "status", None) == "BOOKED"
            and by_key == 1
            and by_intake == 1
            and status is not None
            and str(status.status) == "COMPLETED"
        ),
    }


async def handoff_scenario(fid: uuid.UUID) -> dict[str, Any]:
    original = set_handoff_timing(fid, ack_minutes=1, max_reminders=1)
    try:
        worker = start_worker("handoff_1")
        escalation_id = escalate(fid)
        await asyncio.to_thread(
            wait_for,
            lambda: notifications(escalation_id) == ["NEW_ESCALATION"],
            60,
            "the first alert",
        )
        kill(worker)  # crash while the workflow waits on its acknowledgement timer
        await asyncio.sleep(5)
        worker = start_worker("handoff_2")
        try:
            done = await asyncio.to_thread(
                wait_for,
                lambda: (
                    (e := execution(f"handoff-{escalation_id}")) is not None
                    and str(e.status) == "COMPLETED"
                    and e
                ),
                240,
                "the handoff to finish",
            )
        finally:
            kill(worker)
        kinds = notifications(escalation_id)
        return {
            "notifications": kinds,
            "result": done.result,
            "passed": kinds == ["NEW_ESCALATION", "REMINDER", "UNACKNOWLEDGED_FALLBACK"]
            and done.result == "UNACKNOWLEDGED",
        }
    finally:
        restore_config(fid, original)


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def outbox_scenario(fid: uuid.UUID) -> dict[str, Any]:
    compose("stop", "temporal")
    try:
        escalation_id = escalate(fid)  # the business transaction does not need Temporal
        queued = execution(f"handoff-{escalation_id}")
        queued_status = str(queued.status) if queued else None
    finally:
        compose("start", "temporal")
    await asyncio.to_thread(time.sleep, 8)
    worker = start_worker("outbox")
    try:
        await asyncio.to_thread(
            wait_for,
            lambda: "NEW_ESCALATION" in notifications(escalation_id),
            120,
            "the queued handoff to be dispatched",
        )
    finally:
        kill(worker)
    started = execution(f"handoff-{escalation_id}")
    return {
        "status_while_temporal_down": queued_status,
        "status_after_recovery": str(started.status) if started else None,
        "notifications": notifications(escalation_id),
        "passed": queued_status == "REQUESTED"
        and notifications(escalation_id) == ["NEW_ESCALATION"],
    }


async def main(report: Path | None) -> int:
    fid = firm_id()
    results = {"started_at": datetime.now(UTC).isoformat(), "task_queue": QUEUE}
    for name, scenario in (
        ("booking", booking_scenario),
        ("handoff", handoff_scenario),
        ("outbox", outbox_scenario),
    ):
        try:
            results[name] = await scenario(fid)
        except Exception as exc:
            results[name] = {"passed": False, "error": repr(exc)}
        print(name, "PASS" if results[name]["passed"] else "FAIL", flush=True)
    print(json.dumps(results, indent=2, default=str))
    if report:
        report.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return 0 if all(results[n]["passed"] for n in ("booking", "handoff", "outbox")) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    sys.exit(asyncio.run(main(parser.parse_args().report)))
