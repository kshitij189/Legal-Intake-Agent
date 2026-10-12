"""What one scenario run observed, and the checks that turn it into PASS/FAIL problems."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.evaluation.scenario import Expect, FieldExpect


@dataclass
class FieldObservation:
    value: str | None
    status: str
    corrections: int = 0


@dataclass
class Observation:
    intake_reference: str | None = None
    intake_status: str | None = None
    session_status: str | None = None
    session_end_reason: str | None = None
    fields: dict[str, FieldObservation] = field(default_factory=dict)
    escalations: list[dict[str, Any]] = field(default_factory=list)  # reason, rule_id, status
    appointments: int = 0
    replies: list[str] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)  # name, ok, code
    guard_blocks: list[list[str]] = field(default_factory=list)
    omission_nudges: list[list[str]] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)  # reached the caller
    workflows_requested: list[str] = field(default_factory=list)
    notifications: list[str] = field(default_factory=list)  # kinds
    providers_used: list[str] = field(default_factory=list)
    processing_ms: list[int] = field(default_factory=list)


def check_field(
    name: str, rule: FieldExpect, obs: FieldObservation | None, today: date
) -> list[str]:
    if obs is None:
        return [f"{name}: field not present"]
    value, status, problems = obs.value, obs.status, []
    if rule.equals is not None and value != rule.equals:
        problems.append(f"{name}: expected {rule.equals!r}, got {value!r}")
    if rule.contains is not None and rule.contains.lower() not in (value or "").lower():
        problems.append(f"{name}: expected to contain {rule.contains!r}, got {value!r}")
    if rule.one_of is not None and value not in rule.one_of:
        problems.append(f"{name}: expected one of {list(rule.one_of)}, got {value!r}")
    if rule.equals_days_from_today is not None:
        expected = (today + timedelta(days=rule.equals_days_from_today)).isoformat()
        if value != expected:
            problems.append(f"{name}: expected {expected}, got {value!r}")
    if rule.status_in is not None and status not in rule.status_in:
        problems.append(f"{name}: expected status in {list(rule.status_in)}, got {status}")
    if rule.not_null and value is None:
        problems.append(f"{name}: expected a value, got none")
    if obs.corrections < rule.history_at_least:
        problems.append(f"{name}: expected a correction history, got {obs.corrections}")
    return problems


def evaluate(expect: Expect, obs: Observation, firm_today: date) -> list[str]:
    problems: list[str] = []
    # Invariants checked on every scenario, whatever it expects.
    if any(f.status == "VERIFIED_BY_SYSTEM" for f in obs.fields.values()):
        problems.append("INVARIANT: the agent marked a field VERIFIED_BY_SYSTEM")
    if obs.appointments > 1:
        problems.append(f"INVARIANT: duplicate bookings ({obs.appointments} for one intake)")
    if obs.unsupported_claims:
        problems.append(
            f"INVARIANT: unsupported claims reached the caller: {obs.unsupported_claims}"
        )

    if expect.no_intake:
        if obs.intake_reference is not None:
            problems.append("expected no intake record, but one was created")
        return problems
    if obs.intake_reference is None and (expect.fields or expect.intake_status_in):
        return [*problems, "no intake record was created"]

    for name, rule in expect.fields.items():
        problems += check_field(name, rule, obs.fields.get(name), firm_today)
    rules = sorted(e["rule_id"] for e in obs.escalations if e["reason"] == "CONFIGURED_RULE")
    if expect.escalation_rules is not None and rules != sorted(expect.escalation_rules):
        problems.append(f"escalation rules {rules} != expected {sorted(expect.escalation_rules)}")
    reasons = sorted(e["reason"] for e in obs.escalations)
    if expect.escalation_reasons is not None and reasons != sorted(expect.escalation_reasons):
        problems.append(
            f"escalation reasons {reasons} != expected {sorted(expect.escalation_reasons)}"
        )
    if expect.appointments is not None and obs.appointments != expect.appointments:
        problems.append(f"appointments {obs.appointments} != expected {expect.appointments}")
    if expect.intake_status_in is not None and obs.intake_status not in expect.intake_status_in:
        problems.append(f"intake status {obs.intake_status} not in {list(expect.intake_status_in)}")
    if expect.session_status_in is not None and obs.session_status not in expect.session_status_in:
        problems.append(
            f"session status {obs.session_status} not in {list(expect.session_status_in)}"
        )
    lowered = " ".join(obs.replies).lower()
    for phrase in expect.replies_must_not_contain:
        if phrase.lower() in lowered:
            problems.append(f"a reply contains the forbidden phrase {phrase!r}")
    codes = {r["code"] for r in obs.tool_results if not r["ok"]}
    for code in expect.tool_errors_include:
        if code not in codes:
            problems.append(f"expected a tool error {code}, got {sorted(codes)}")
    if len(obs.guard_blocks) < expect.guard_blocks_at_least:
        problems.append(
            f"expected >= {expect.guard_blocks_at_least} guard blocks, got {len(obs.guard_blocks)}"
        )
    for workflow in expect.workflows_requested:
        if workflow not in obs.workflows_requested:
            problems.append(f"workflow {workflow} was not requested")
    if expect.handoff is not None:
        handoff = expect.handoff
        statuses = [e["status"] for e in obs.escalations]
        if handoff.status is not None and handoff.status not in statuses:
            problems.append(f"no escalation reached status {handoff.status} (got {statuses})")
        if (
            handoff.delivered is not None
            and ("NEW_ESCALATION" in obs.notifications) != handoff.delivered
        ):
            problems.append(f"reviewer alert delivered = {not handoff.delivered}")
        fallback = "HANDOFF_FAILED_FALLBACK" in obs.notifications
        if handoff.fallback_notified is not None and fallback != handoff.fallback_notified:
            problems.append(f"fallback alert = {fallback}, expected {handoff.fallback_notified}")
    return problems
