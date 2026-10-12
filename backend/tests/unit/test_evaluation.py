"""Evaluation harness building blocks: scenario schema, scripted model, checks, metrics, report."""

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.llm_client import LLMUnavailable
from app.evaluation.checks import FieldObservation, Observation, check_field, evaluate
from app.evaluation.metrics import FAIL, PASS, SKIPPED, ScenarioResult, aggregate, result_from_dict
from app.evaluation.report import markdown, write_reports
from app.evaluation.scenario import Expect, FieldExpect, Scenario, ScriptStep, load_scenarios
from app.evaluation.scripted_llm import ScenarioLLM, ScriptExhausted

TODAY = date(2026, 10, 12)
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "scenarios"


def scenario(**overrides: object) -> Scenario:
    base: dict[str, object] = {
        "id": "T01_test",
        "category": "test",
        "blueprint_case": "test",
        "description": "test",
        "tracks": ["system"],
        "caller_turns": ["hello"],
        "scripted_model": [{"say": "hi"}],
        "expect": {},
    }
    return Scenario.model_validate({**base, **overrides})


# --- scenario schema ------------------------------------------------------------------------


def test_shipped_scenarios_load_and_cover_every_blueprint_category() -> None:
    scenarios = load_scenarios(FIXTURES / "transcript_scenarios.json")
    categories = {s.category for s in scenarios}
    assert {
        "normal_enquiry",
        "urgent_deadline",
        "ambiguous_date",
        "missing_phone",
        "refusal",
        "legal_advice",
        "calendar_timeout",
        "duplicate_booking",
        "disconnect",
        "review_queue_outage",
        "unsupported_category",
        "interruption_correction",
    } <= categories
    assert any(s.critical for s in scenarios)
    assert any("live" in s.tracks for s in scenarios)


def test_script_step_needs_exactly_one_action() -> None:
    with pytest.raises(ValidationError):
        ScriptStep.model_validate({"say": "a", "outage": "b"})
    with pytest.raises(ValidationError):
        ScriptStep.model_validate({})


def test_system_track_requires_a_script() -> None:
    with pytest.raises(ValidationError, match="scripted_model"):
        scenario(scripted_model=[])


def test_workflow_faults_are_system_only() -> None:
    with pytest.raises(ValidationError, match="system track"):
        scenario(tracks=["system", "live"], faults={"notifier": "unavailable"})


def test_unknown_keys_and_bad_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        scenario(expect={"appointment_booked": True})
    with pytest.raises(ValidationError):
        scenario(id="bad id")


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    raw = scenario().model_dump(mode="json", by_alias=True)
    path = tmp_path / "s.json"
    path.write_text(json.dumps([raw, raw]), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_scenarios(path)


def test_needs_temporal() -> None:
    assert not scenario().needs_temporal
    assert scenario(durable_booking=True).needs_temporal
    assert scenario(expect={"handoff": {"delivered": True}}).needs_temporal


# --- scripted model -------------------------------------------------------------------------


def steps(*raw: dict[str, object]) -> list[ScriptStep]:
    return [ScriptStep.model_validate(s) for s in raw]


def test_scripted_model_resolves_dates_and_offered_slots() -> None:
    llm = ScenarioLLM(
        steps(
            {"tools": [{"name": "save", "args": {"value": "{{today+2}}", "n": 3}}]},
            {"tools": [{"name": "book", "args": {"slot_id": "{{offered_slot:1}}"}}]},
            {"say": "done"},
        ),
        TODAY,
    )
    first = llm.complete([], [])
    assert json.loads(first.tool_calls[0].arguments) == {"value": "2026-10-14", "n": 3}
    slots = {"ok": True, "data": {"slots": [{"slot_id": "a"}, {"slot_id": "b"}]}}
    second = llm.complete([{"role": "tool", "content": json.dumps(slots)}], [])
    assert json.loads(second.tool_calls[0].arguments) == {"slot_id": "b"}
    assert llm.complete([], []).content == "done"
    with pytest.raises(ScriptExhausted):
        llm.complete([], [])


def test_scripted_model_negative_offsets_and_outage() -> None:
    llm = ScenarioLLM(
        steps({"tools": [{"name": "x", "args": {"d": "{{today-1}}"}}]}, {"outage": "down"}), TODAY
    )
    assert json.loads(llm.complete([], []).tool_calls[0].arguments) == {"d": "2026-10-11"}
    with pytest.raises(LLMUnavailable):
        llm.complete([], [])


def test_offered_slot_without_availability_is_a_script_error() -> None:
    llm = ScenarioLLM(steps({"tools": [{"name": "b", "args": {"s": "{{offered_slot:0}}"}}]}), TODAY)
    with pytest.raises(ScriptExhausted, match="availability"):
        llm.complete([], [])


# --- checks ---------------------------------------------------------------------------------


def field(
    value: str | None, status: str = "REPORTED_BY_CALLER", corrections: int = 0
) -> FieldObservation:
    return FieldObservation(value, status, corrections)


@pytest.mark.parametrize(
    ("rule", "obs", "ok"),
    [
        ({"equals": "a"}, field("a"), True),
        ({"equals": "a"}, field("b"), False),
        ({"contains": "priya"}, field("Priya Sharma"), True),
        ({"in": ["X", "Y"]}, field("Z"), False),
        ({"equals_days_from_today": 1}, field("2026-10-13"), True),
        ({"equals_days_from_today": 1}, field("2026-10-14"), False),
        ({"status_in": ["DECLINED_BY_CALLER"]}, field(None, "MISSING"), False),
        ({"not_null": True}, field(None), False),
        ({"history_at_least": 1}, field("a", corrections=1), True),
        ({"history_at_least": 1}, field("a"), False),
    ],
)
def test_check_field(rule: dict[str, object], obs: FieldObservation, ok: bool) -> None:
    problems = check_field("f", FieldExpect.model_validate(rule), obs, TODAY)
    assert (not problems) is ok


def test_missing_field_is_a_problem() -> None:
    assert check_field("f", FieldExpect(), None, TODAY) == ["f: field not present"]


def test_invariants_fail_every_scenario() -> None:
    obs = Observation(
        intake_reference="INT-1",
        fields={"phone": field("1", "VERIFIED_BY_SYSTEM")},
        appointments=2,
        unsupported_claims=["false_booking_confirmation"],
    )
    problems = evaluate(Expect(), obs, TODAY)
    assert len([p for p in problems if p.startswith("INVARIANT")]) == 3


def test_expectations() -> None:
    obs = Observation(
        intake_reference="INT-1",
        intake_status="PENDING_REVIEW",
        session_status="COMPLETED",
        escalations=[
            {"reason": "CONFIGURED_RULE", "rule_id": "imminent_deadline", "status": "PENDING"}
        ],
        replies=["You should sue them."],
        tool_results=[{"name": "book_consultation", "ok": False, "code": "ALREADY_BOOKED"}],
        notifications=["HANDOFF_FAILED_FALLBACK"],
    )
    expect = Expect.model_validate(
        {
            "escalation_rules": [],
            "appointments": 1,
            "intake_status_in": ["INCOMPLETE"],
            "replies_must_not_contain": ["should sue"],
            "tool_errors_include": ["ALREADY_BOOKED", "SLOT_NOT_OFFERED"],
            "guard_blocks_at_least": 1,
            "workflows_requested": ["HANDOFF"],
            "handoff": {"delivered": True, "fallback_notified": False, "status": "FAILED"},
        }
    )
    problems = evaluate(expect, obs, TODAY)
    assert len(problems) == 10, problems
    assert not evaluate(Expect(escalation_rules=("imminent_deadline",)), obs, TODAY)


def test_no_intake_expectation() -> None:
    assert evaluate(Expect(no_intake=True), Observation(), TODAY) == []
    assert evaluate(Expect(no_intake=True), Observation(intake_reference="INT-1"), TODAY)
    assert evaluate(Expect(fields={"phone": FieldExpect()}), Observation(), TODAY) == [
        "no intake record was created"
    ]


# --- metrics and report ---------------------------------------------------------------------


def test_aggregate_metrics() -> None:
    critical = scenario(
        id="U01_urgent", critical=True, expect={"escalation_rules": ["imminent_deadline"]}
    )
    plain = scenario(id="N01_plain")
    hit = Observation(
        escalations=[
            {"reason": "CONFIGURED_RULE", "rule_id": "imminent_deadline", "status": "PENDING"}
        ],
        tool_results=[{"name": "book_consultation", "ok": True, "code": None}],
        appointments=1,
        replies=["Hello **there**? Which one?"],
        processing_ms=[100, 300],
    )
    miss = Observation(unsupported_claims=["false_booking_confirmation"], guard_blocks=[["x"]])
    results = [
        ScenarioResult(
            "U01_urgent",
            "t",
            "live",
            PASS,
            observation=hit,
            critical=True,
            field_checks=4,
            field_failures=1,
        ),
        ScenarioResult("N01_plain", "t", "live", FAIL, observation=miss),
        ScenarioResult("N01_plain", "t", "live", SKIPPED),
    ]
    m = aggregate(results, {"U01_urgent": critical, "N01_plain": plain})
    assert m["scenarios"] == {"PASS": 1, "FAIL": 1, "SKIPPED": 1, "ERROR": 0}
    assert m["field_extraction_accuracy"] == 0.75
    assert m["critical_escalation_recall"] == 1.0
    assert m["false_booking_confirmations_reaching_caller"] == 1
    assert m["unsupported_claims_blocked_by_guard"] == 1
    assert m["duplicate_booking_rate"] == 0.0
    assert m["turn_processing_ms_p95"] == 300
    signals = m["conversation_signals"]
    assert signals["replies_with_multiple_questions"] == 1
    assert signals["replies_with_markdown"] == 1


def test_result_round_trip_and_reports(tmp_path: Path) -> None:
    result = ScenarioResult(
        "N01_plain",
        "t",
        "system",
        FAIL,
        problems=["a | b"],
        observation=Observation(fields={"phone": field("1")}),
    )
    assert result_from_dict(json.loads(json.dumps(asdict(result)))) == result
    md = markdown({"run_id": "r"}, [result], {"system": {"duplicate_booking_rate": 0.0}})
    assert "| N01_plain | system | **FAIL** | a / b |" in md
    json_path, md_path = write_reports(tmp_path, "x", {"run_id": "r"}, [result], {})
    assert json.loads(json_path.read_text(encoding="utf-8"))["results"][0]["status"] == FAIL
    assert md_path.exists()
