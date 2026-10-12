"""Transcript-mode evaluation (blueprint §13 Mode A): system track (scripted model) and live track
(real LLM chain). Runs the real conversation, tools, database and - when a scenario needs them -
a Temporal worker with the scenario's injected faults."""

import asyncio
import contextlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import Client

from app.agent.conversation import IntakeConversation
from app.agent.llm_client import LLMClient
from app.agent.reply_guard import check_reply
from app.agent.tool_registry import REGISTRY
from app.config import Settings
from app.db.seed import seed_demo_data
from app.domain import intake_service
from app.domain.conversation_state import build_conversation_state
from app.domain.firm_config_service import firm_today, get_active_firm
from app.evaluation.checks import FieldObservation, Observation, evaluate
from app.evaluation.metrics import ERROR, FAIL, PASS, SKIPPED, ScenarioResult
from app.evaluation.scenario import Scenario, Track
from app.evaluation.scripted_llm import ScenarioLLM, ScriptExhausted
from app.models import (
    Appointment,
    AuditEvent,
    CallSession,
    Escalation,
    Firm,
    IntakeRecord,
    StaffNotification,
    TranscriptTurn,
    WorkflowExecution,
)
from app.models.enums import AppointmentStatus, WorkflowStatus
from app.tools.executor import ToolSpec
from app.workflows.activities import IntakeActivities
from app.workflows.worker import build_worker, dispatch_pending

logger = logging.getLogger(__name__)
T = TypeVar("T")
HANDOFF_WAIT_SECONDS = 60


def create_eval_firm(sessions: sessionmaker[Session], run_id: str) -> uuid.UUID:
    """A fresh firm per evaluation run: isolated data and a full calendar."""
    with sessions() as s, s.begin():
        return seed_demo_data(s, slug=f"eval-{run_id}", now=datetime.now(UTC)).firm_id


def retire_eval_firm(sessions: sessionmaker[Session], firm_id: uuid.UUID) -> None:
    """Deactivate a finished run's firm: its data stays for inspection, but the regular outbox
    dispatcher never starts its leftover workflow requests (e.g. post-call follow-ups)."""
    with sessions() as s, s.begin():
        s.get_one(Firm, firm_id).is_active = False


class EvalTemporal:
    """A Temporal worker on its own task queue (with the scenario's faults), run in a background
    thread. Only the evaluation firm's outbox rows are dispatched to it."""

    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        *,
        calendar_fault: str | None,
        notifier_fault: str | None,
    ) -> None:
        self.queue = f"eval-{uuid.uuid4().hex[:10]}"
        self._sessions = sessions
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self.client: Client | None = None
        self._activities = IntakeActivities(
            sessions, calendar_fault=calendar_fault, notifier_fault=notifier_fault
        )
        self._settings = settings
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(30):
            raise RuntimeError("evaluation Temporal worker did not start")

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        self.client = await Client.connect(
            self._settings.temporal_address, namespace=self._settings.temporal_namespace
        )
        self._stop = asyncio.Event()
        async with build_worker(self.client, self._activities, self.queue):
            self._ready.set()
            await self._stop.wait()

    def dispatch(self, firm_id: uuid.UUID) -> int:
        assert self.client is not None
        return dispatch_pending(self._sessions, self.client, self._loop, self.queue, [firm_id])

    def terminate_open_workflows(self, workflow_ids: list[str]) -> None:
        async def terminate() -> None:
            assert self.client is not None
            for workflow_id in workflow_ids:
                with contextlib.suppress(Exception):  # already finished
                    await self.client.get_workflow_handle(workflow_id).terminate("evaluation end")

        asyncio.run_coroutine_threadsafe(terminate(), self._loop).result(timeout=30)

    def call(self, coro: Coroutine[Any, Any, T], timeout: float = 30) -> T:
        """Run a coroutine (e.g. a Temporal client call) on the worker's event loop."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def close(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=30)


def failing_registry(tool_name: str) -> dict[str, ToolSpec]:
    """Fault: this tool's database work fails (simulated outage) - the executor must not report
    success and nothing may be persisted."""

    def fail(*_: Any) -> dict[str, Any]:
        raise OperationalError("simulated outage", {}, Exception("database unavailable"))

    registry = dict(REGISTRY)
    spec = registry[tool_name]
    registry[tool_name] = ToolSpec(spec.name, spec.description, spec.input_model, fail, spec.verify)
    return registry


@dataclass
class EvalContext:
    settings: Settings
    sessions: sessionmaker[Session]
    firm_id: uuid.UUID
    live_llm: Callable[[], LLMClient] | None = None


def run_scenario(scenario: Scenario, track: Track, ctx: EvalContext) -> ScenarioResult:
    started = time.monotonic()
    with ctx.sessions() as s:
        today = firm_today(get_active_firm(s, ctx.firm_id), datetime.now(UTC))
    result = ScenarioResult(
        scenario.id, scenario.category, track, ERROR, critical=scenario.critical
    )
    temporal = None
    try:
        if scenario.needs_temporal and track == "system":
            temporal = EvalTemporal(
                ctx.settings,
                ctx.sessions,
                calendar_fault=scenario.faults.calendar,
                notifier_fault=scenario.faults.notifier,
            )
        if track == "system":
            llm: LLMClient = ScenarioLLM(scenario.scripted_model, today)
        else:
            assert ctx.live_llm is not None
            llm = ctx.live_llm()
        convo = IntakeConversation(
            llm=llm,
            sessions=ctx.sessions,
            firm_id=ctx.firm_id,
            durable_booking=scenario.durable_booking and temporal is not None,
            booking_task_queue=temporal.queue if temporal else None,
            registry=failing_registry(scenario.faults.db_outage_tool)
            if scenario.faults.db_outage_tool
            else None,
        )
        convo.start()
        replies: list[str] = []
        for turn in scenario.caller_turns:
            if convo.ended:
                break
            replies.append(convo.respond(turn))
        if not convo.ended:
            convo.end(disconnected=scenario.disconnect)

        if temporal is not None and scenario.expect.handoff is not None:
            assert convo.call_session_id is not None
            wait_for_handoff(ctx, temporal, convo.call_session_id)
        observation = observe(ctx, convo, replies)
        if temporal is not None:
            temporal.terminate_open_workflows(open_workflow_ids(ctx))

        if track == "live" and live_provider_outage(observation, scenario):
            result.status = SKIPPED
            result.note = f"model provider unavailable: {observation.session_end_reason}"
        else:
            result.problems = evaluate(scenario.expect, observation, today)
            result.status = PASS if not result.problems else FAIL
        result.observation = observation
        result.field_checks = len(scenario.expect.fields)
        result.field_failures = len(
            {p.split(":")[0] for p in result.problems if p.split(":")[0] in scenario.expect.fields}
        )
    except ScriptExhausted as exc:
        result.status, result.note = ERROR, f"scenario script exhausted: {exc}"
    except Exception as exc:  # the harness must report, not crash
        logger.exception("scenario_error")
        result.status, result.note = ERROR, repr(exc)
    finally:
        if temporal is not None:
            temporal.close()
        result.duration_s = round(time.monotonic() - started, 1)
    return result


def live_provider_outage(obs: Observation, scenario: Scenario) -> bool:
    scripted_outage = any(step.outage for step in scenario.scripted_model)
    return bool(
        obs.session_end_reason
        and obs.session_end_reason.startswith("llm_unavailable")
        and not scripted_outage
    )


def wait_for_handoff(ctx: EvalContext, temporal: EvalTemporal, call_session_id: uuid.UUID) -> None:
    """Dispatch the evaluation firm's outbox and wait until each of this call's escalations has
    either alerted a reviewer or failed over."""
    deadline = time.monotonic() + HANDOFF_WAIT_SECONDS
    while time.monotonic() < deadline:
        temporal.dispatch(ctx.firm_id)
        with ctx.sessions() as s:
            intake_id = s.get_one(CallSession, call_session_id).intake_id
            escalation_ids = s.scalars(
                select(Escalation.id).where(Escalation.intake_id == intake_id)
            ).all()
            settled = 0
            for escalation_id in escalation_ids:
                kinds = {
                    str(k)
                    for k in s.scalars(
                        select(StaffNotification.kind).where(
                            StaffNotification.escalation_id == escalation_id
                        )
                    )
                }
                if kinds & {"NEW_ESCALATION", "HANDOFF_FAILED_FALLBACK"}:
                    settled += 1
        if escalation_ids and settled == len(escalation_ids):
            return
        time.sleep(1)


def open_workflow_ids(ctx: EvalContext) -> list[str]:
    with ctx.sessions() as s:
        return list(
            s.scalars(
                select(WorkflowExecution.workflow_id).where(
                    WorkflowExecution.firm_id == ctx.firm_id,
                    WorkflowExecution.status.in_(
                        [WorkflowStatus.REQUESTED, WorkflowStatus.STARTED]
                    ),
                )
            ).all()
        )


def observe(ctx: EvalContext, convo: IntakeConversation, replies: list[str]) -> Observation:
    obs = Observation(replies=replies, providers_used=list(convo.providers_used))
    obs.tool_results = [
        {
            "name": t.tool,
            "ok": (r := json.loads(t.result))["ok"],
            "code": None if r["ok"] else r["error"]["code"],
        }
        for t in convo.trace
    ]
    assert convo.call_session_id is not None
    with ctx.sessions() as s:
        call = s.get_one(CallSession, convo.call_session_id)
        obs.session_status, obs.session_end_reason = str(call.status), call.end_reason
        events = s.scalars(select(AuditEvent).where(AuditEvent.entity_id == call.id)).all()
        obs.guard_blocks = [
            e.details["violations"] for e in events if e.action == "agent.reply_blocked"
        ]
        obs.omission_nudges = [
            e.details["omissions"] for e in events if e.action == "agent.omission_nudged"
        ]
        state = build_conversation_state(s, firm_id=ctx.firm_id, intake_id=call.intake_id)
        obs.unsupported_claims = sorted({v for r in replies for v in check_reply(r, state)})
        if call.intake_id is None:
            return obs
        intake = s.get_one(IntakeRecord, call.intake_id)
        obs.intake_reference, obs.intake_status = intake.reference, str(intake.status)
        obs.fields = {
            k: FieldObservation(e.value, str(e.status), len(e.history))
            for k, e in intake_service.intake_fields(intake).items()
        }
        escalations = s.scalars(select(Escalation).where(Escalation.intake_id == intake.id)).all()
        obs.escalations = [
            {"reason": str(e.reason_code), "rule_id": e.rule_id, "status": str(e.status)}
            for e in escalations
        ]
        obs.appointments = (
            s.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(
                    Appointment.intake_id == intake.id,
                    Appointment.status == AppointmentStatus.BOOKED,
                )
            )
            or 0
        )
        obs.workflows_requested = sorted(
            {
                str(w)
                for w in s.scalars(
                    select(WorkflowExecution.workflow_type).where(
                        WorkflowExecution.entity_id.in_(
                            [call.id, intake.id, *[e.id for e in escalations]]
                        )
                    )
                )
            }
        )
        obs.notifications = [
            str(k)
            for k in s.scalars(
                select(StaffNotification.kind).where(
                    StaffNotification.escalation_id.in_([e.id for e in escalations])
                )
            )
        ]
        obs.processing_ms = [
            ms
            for ms in s.scalars(
                select(TranscriptTurn.processing_ms).where(
                    TranscriptTurn.call_session_id == call.id
                )
            )
            if ms is not None
        ]
    return obs
