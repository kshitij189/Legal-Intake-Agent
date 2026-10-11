"""Run labelled scripted conversations through the real agent (Groq + PostgreSQL) and check the
persisted state. Phase 2 evidence; Phase 6 grows this into the full evaluation harness.

Usage: .venv\\Scripts\\python scripts/run_scripted_conversations.py [--only ID] [--report FILE]
Exit code 0 only if every scenario passes. Results depend on a live model and can vary per run.
"""

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agent.conversation import IntakeConversation
from app.agent.llm_client import build_llm_client
from app.config import REPO_ROOT, get_settings
from app.db.session import make_engine, make_session_factory
from app.domain import appointment_service, escalation_service, intake_service
from app.domain.firm_config_service import firm_today
from app.models import CallSession, Firm, IntakeRecord
from app.models.enums import EscalationReason, FieldStatus

SCENARIOS = REPO_ROOT / "fixtures" / "transcripts" / "phase2_scripted.json"
# Heuristic: a reply that sounds like a booking confirmation when nothing is booked.
BOOKING_CLAIM = re.compile(r"\b(is|are|been|have)\s+(now\s+)?(booked|scheduled|confirmed)\b", re.I)


def check_field(name: str, rule: dict[str, Any], entry: Any, today: Any) -> list[str]:
    problems = []
    value, status = entry.value, str(entry.status)
    if "equals" in rule and value != rule["equals"]:
        problems.append(f"{name}: expected {rule['equals']!r}, got {value!r}")
    if "contains" in rule and rule["contains"].lower() not in (value or "").lower():
        problems.append(f"{name}: expected to contain {rule['contains']!r}, got {value!r}")
    if "equals_days_from_today" in rule:
        expected = (today + timedelta(days=rule["equals_days_from_today"])).isoformat()
        if value != expected:
            problems.append(f"{name}: expected {expected}, got {value!r}")
    if "status_in" in rule and status not in rule["status_in"]:
        problems.append(f"{name}: expected status in {rule['status_in']}, got {status}")
    return problems


def run_scenario(scenario: dict[str, Any], sessions: Any, firm: Firm, llm: Any) -> dict[str, Any]:
    convo = IntakeConversation(llm=llm, sessions=sessions, firm_id=firm.id)
    transcript = [{"agent": convo.start()}]
    for turn in scenario["caller_turns"]:
        if convo.ended:
            break
        transcript.append({"caller": turn})
        transcript.append({"agent": convo.respond(turn)})
    end_status = None if convo.ended else str(convo.end())

    expect, problems = scenario["expect"], []
    with sessions() as s:
        call = s.get_one(CallSession, convo.call_session_id)
        intake = s.get(IntakeRecord, call.intake_id) if call.intake_id else None
        if intake is None:
            problems.append("no intake record was created")
            fields: dict[str, Any] = {}
            escalations, appointment = [], None
        else:
            fields = intake_service.intake_fields(intake)
            escalations = escalation_service.list_escalations(
                s, firm_id=firm.id, intake_id=intake.id
            )
            appointment = appointment_service.get_booked_appointment_for_intake(
                s, firm_id=firm.id, intake_id=intake.id
            )
            today = firm_today(firm, datetime.now(UTC))
            for name, rule in expect.get("fields", {}).items():
                problems += check_field(name, rule, fields[name], today)
            if str(intake.status) not in expect["intake_status_in"]:
                problems.append(
                    f"intake status {intake.status} not in {expect['intake_status_in']}"
                )
            if any(e.status is FieldStatus.VERIFIED_BY_SYSTEM for e in fields.values()):
                problems.append("a field was marked VERIFIED_BY_SYSTEM")

        rules = sorted(
            e.rule_id or ""
            for e in escalations
            if e.reason_code is EscalationReason.CONFIGURED_RULE
        )
        if rules != sorted(expect["escalation_rules"]):
            problems.append(f"escalation rules {rules} != expected {expect['escalation_rules']}")
        if (appointment is not None) != expect["appointment_booked"]:
            problems.append(f"appointment booked = {appointment is not None}")
        replies = " ".join(t["agent"] for t in transcript if "agent" in t)
        if appointment is None and BOOKING_CLAIM.search(replies):
            problems.append("possible false booking confirmation in agent replies")

        return {
            "id": scenario["id"],
            "passed": not problems,
            "problems": problems,
            "session_status": str(call.status),
            "end_status": end_status,
            "intake_reference": intake.reference if intake else None,
            "fields": {k: {"value": v.value, "status": str(v.status)} for k, v in fields.items()},
            "escalations": [
                {"reason": str(e.reason_code), "rule_id": e.rule_id, "status": str(e.status)}
                for e in escalations
            ],
            "appointment": appointment.starts_at.isoformat() if appointment else None,
            "llm_providers_used": convo.providers_used,
            "tool_calls": [t.tool for t in convo.trace],
            "tool_errors": [
                json.loads(t.result)["error"]["code"]
                for t in convo.trace
                if not json.loads(t.result)["ok"]
            ],
            "transcript": transcript,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between scenarios")
    args = parser.parse_args()

    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    llm = build_llm_client(settings)
    with sessions() as s:
        firm = s.scalars(select(Firm).where(Firm.slug == settings.demo_firm_slug)).one()

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [sc for sc in scenarios if sc["id"] == args.only]
    results = []
    for i, scenario in enumerate(scenarios):
        if i:
            time.sleep(args.pause)  # stay under provider rate limits
        result = run_scenario(scenario, sessions, firm, llm)
        results.append(result)
        print(
            f"{'PASS' if result['passed'] else 'FAIL'}  {result['id']}  "
            f"({result['intake_reference']}, session {result['session_status']})"
        )
        for problem in result["problems"]:
            print(f"      - {problem}")

    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed (providers {settings.llm_providers})")
    if args.report:
        args.report.write_text(
            json.dumps(
                {
                    "providers": settings.llm_providers,
                    "gemini_model": settings.gemini_model,
                    "groq_model": settings.groq_model,
                    "results": results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
