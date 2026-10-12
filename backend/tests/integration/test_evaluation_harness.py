"""The evaluation harness against the real database: every system-track scenario that does not
need Temporal passes, and deliberately broken behaviour is reported as FAIL (negative controls),
so a green run means something. Temporal-backed scenarios run in scripts/run_evaluations.py."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.agent import reply_guard
from app.config import get_settings
from app.db.session import make_session_factory
from app.domain import policy_engine
from app.evaluation.metrics import ERROR, FAIL, PASS, aggregate
from app.evaluation.runner import EvalContext, create_eval_firm, run_scenario
from app.evaluation.scenario import Scenario, load_scenarios
from tests.integration.conftest import TABLES

SCENARIOS = {
    s.id: s
    for s in load_scenarios(
        Path(__file__).resolve().parents[3] / "fixtures" / "scenarios" / "transcript_scenarios.json"
    )
}
WITHOUT_TEMPORAL = [s for s in SCENARIOS.values() if "system" in s.tracks and not s.needs_temporal]


@pytest.fixture(scope="module")
def eval_ctx(engine: Engine) -> Iterator[EvalContext]:
    sessions = make_session_factory(engine)
    firm_id = create_eval_firm(sessions, f"test-{uuid.uuid4().hex[:8]}")
    yield EvalContext(get_settings(), sessions, firm_id)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))


def nothing(*_: object) -> list[Any]:
    return []


def variant(scenario_id: str, **changes: Any) -> Scenario:
    raw = SCENARIOS[scenario_id].model_dump(mode="json", by_alias=True, exclude_none=True)
    return Scenario.model_validate({**raw, **changes})


@pytest.mark.parametrize("scenario", WITHOUT_TEMPORAL, ids=lambda s: s.id)
def test_system_scenario_passes(scenario: Scenario, eval_ctx: EvalContext) -> None:
    result = run_scenario(scenario, "system", eval_ctx)
    assert result.status == PASS, (result.problems, result.note)


def test_wrong_expectations_fail(eval_ctx: EvalContext) -> None:
    raw = SCENARIOS["N01_employment_booking"].model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    raw["expect"]["fields"]["phone"] = {"equals": "0000000"}
    raw["expect"]["appointments"] = 0
    result = run_scenario(Scenario.model_validate(raw), "system", eval_ctx)
    assert result.status == FAIL
    assert any(p.startswith("phone:") for p in result.problems)
    assert any(p.startswith("appointments 1") for p in result.problems)
    assert result.field_failures == 1


def test_disabled_reply_guard_is_caught(
    eval_ctx: EvalContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sabotage: the conversation's guard lets everything through. The harness re-checks the
    replies against the final database state and fails the scenario."""
    monkeypatch.setattr(reply_guard, "check_reply", nothing)
    result = run_scenario(SCENARIOS["A01_false_booking_claim"], "system", eval_ctx)
    assert result.status == FAIL, result.note
    assert any("INVARIANT: unsupported claims" in p for p in result.problems)


def test_disabled_escalation_rules_drop_critical_recall(
    eval_ctx: EvalContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(policy_engine, "evaluate_rules", nothing)
    scenario = SCENARIOS["U03_vacate_by_tomorrow"]
    result = run_scenario(scenario, "system", eval_ctx)
    assert result.status == FAIL
    assert aggregate([result], {scenario.id: scenario})["critical_escalation_recall"] == 0.0


def test_script_mismatch_is_an_error_not_a_pass(eval_ctx: EvalContext) -> None:
    short = variant("N04_consumer_short", scripted_model=[{"say": "Hello."}])
    result = run_scenario(short, "system", eval_ctx)
    assert result.status == ERROR
    assert "exhausted" in (result.note or "")
