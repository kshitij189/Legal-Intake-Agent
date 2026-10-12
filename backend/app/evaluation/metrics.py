"""Aggregate metrics (blueprint §13 "What to measure") over scenario results.

Model-quality metrics (extraction, recall, conversation signals) come from the live track;
system guarantees (false confirmations, duplicates, recovery, handoff integrity) are counted on
every track that ran.
"""

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.checks import FieldObservation, Observation
from app.evaluation.scenario import Scenario

PASS, FAIL, SKIPPED, ERROR = "PASS", "FAIL", "SKIPPED", "ERROR"
_INTERNAL_ID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-|\b(save_intake_answer|book_consultation|field_key)\b"
)
_MARKDOWN = re.compile(r"\*\*|^#|^\s*[-*] ", re.M)


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    track: str
    status: str  # PASS | FAIL | SKIPPED | ERROR
    problems: list[str] = field(default_factory=list)
    observation: Observation | None = None
    critical: bool = False
    field_checks: int = 0
    field_failures: int = 0
    duration_s: float = 0.0
    note: str | None = None
    details: dict[str, Any] = field(default_factory=dict)  # track-specific measurements (audio)


def result_from_dict(raw: dict[str, Any]) -> ScenarioResult:
    """Rebuild a result written by dataclasses.asdict (used by --resume)."""
    data = dict(raw)
    if data.get("observation") is not None:
        obs = dict(data["observation"])
        obs["fields"] = {k: FieldObservation(**v) for k, v in obs["fields"].items()}
        data["observation"] = Observation(**obs)
    return ScenarioResult(**data)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct * (len(ordered) - 1)))]


def aggregate(results: list[ScenarioResult], scenarios: dict[str, Scenario]) -> dict[str, Any]:
    ran = [r for r in results if r.status in (PASS, FAIL) and r.observation is not None]
    obs = [r.observation for r in ran if r.observation is not None]

    critical = [r for r in ran if r.critical]
    critical_hits = 0
    for r in critical:
        expected = set(scenarios[r.scenario_id].expect.escalation_rules or ())
        got = {e["rule_id"] for e in (r.observation.escalations if r.observation else [])}
        critical_hits += int(expected <= got)

    booking_attempts = [
        o for o in obs if any(t["name"] == "book_consultation" for t in o.tool_results)
    ]
    tool_calls = [t for o in obs for t in o.tool_results]
    unexpected_internal = [t for t in tool_calls if t["code"] == "INTERNAL_ERROR"]
    injected = sum(1 for r in ran if scenarios[r.scenario_id].faults.db_outage_tool)
    fault_runs = [
        r
        for r in ran
        if any(
            getattr(scenarios[r.scenario_id].faults, f)
            for f in ("calendar", "notifier", "db_outage_tool")
        )
        or any(s.outage for s in scenarios[r.scenario_id].scripted_model)
        or scenarios[r.scenario_id].disconnect
    ]
    handoff_runs = [r for r in ran if scenarios[r.scenario_id].expect.handoff is not None]
    replies = [reply for o in obs for reply in o.replies]

    return {
        "scenarios": {
            s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIPPED, ERROR)
        },
        "field_extraction_accuracy": _ratio(
            sum(r.field_checks - r.field_failures for r in ran),
            sum(r.field_checks for r in ran),
        ),
        "critical_escalation_recall": _ratio(critical_hits, len(critical)),
        "false_booking_confirmations_reaching_caller": sum(
            1 for o in obs for c in o.unsupported_claims if c == "false_booking_confirmation"
        ),
        "unsupported_claims_blocked_by_guard": sum(len(o.guard_blocks) for o in obs),
        "duplicate_booking_rate": _ratio(
            sum(1 for o in booking_attempts if o.appointments > 1), len(booking_attempts)
        ),
        "tool_calls": len(tool_calls),
        "tool_calls_rejected_by_validation": sum(
            1 for t in tool_calls if not t["ok"] and t["code"] != "INTERNAL_ERROR"
        ),
        "tool_internal_errors_unexpected": max(0, len(unexpected_internal) - injected),
        "recovery_success": _ratio(sum(1 for r in fault_runs if r.status == PASS), len(fault_runs)),
        "handoff_integrity": _ratio(
            sum(1 for r in handoff_runs if r.status == PASS), len(handoff_runs)
        ),
        "omission_nudges": sum(len(o.omission_nudges) for o in obs),
        "turn_processing_ms_p50": _percentile([m for o in obs for m in o.processing_ms], 0.5),
        "turn_processing_ms_p95": _percentile([m for o in obs for m in o.processing_ms], 0.95),
        "conversation_signals": {
            "replies": len(replies),
            "mean_words_per_reply": round(statistics.mean(len(r.split()) for r in replies), 1)
            if replies
            else None,
            "replies_with_multiple_questions": sum(1 for r in replies if r.count("?") > 1),
            "replies_with_markdown": sum(1 for r in replies if _MARKDOWN.search(r)),
            "replies_exposing_internal_ids": sum(1 for r in replies if _INTERNAL_ID.search(r)),
            "consecutive_repeated_replies": sum(
                1 for o in obs for a, b in zip(o.replies, o.replies[1:], strict=False) if a == b
            ),
        },
    }
