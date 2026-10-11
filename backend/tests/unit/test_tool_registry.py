import json
from datetime import date
from typing import Any

from app.agent import prompts
from app.agent.tool_registry import REGISTRY, tool_definitions
from tests.helpers import demo_config

CONFIG = demo_config()
BLUEPRINT_TOOLS = {
    "get_intake_questions",
    "save_intake_answer",
    "get_intake_status",
    "check_appointment_availability",
    "book_consultation",
    "create_draft_intake",
    "mark_for_human_review",
    "get_handoff_status",
    "finalize_intake_summary",
}


def definitions() -> dict[str, dict[str, Any]]:
    return {d["function"]["name"]: d["function"] for d in tool_definitions(CONFIG)}


def test_exactly_the_nine_blueprint_tools_are_exposed() -> None:
    assert set(REGISTRY) == BLUEPRINT_TOOLS
    assert set(definitions()) == BLUEPRINT_TOOLS
    # No general-purpose escape hatches.
    text = json.dumps(tool_definitions(CONFIG)).lower()
    for forbidden in ("sql", "shell", "execute", "url", "http"):
        assert forbidden not in " ".join(definitions()), forbidden
    assert "intake_id" not in text and "firm_id" not in text  # identity comes from the session


def test_every_schema_is_closed_and_untitled() -> None:
    for name, fn in definitions().items():
        params = fn["parameters"]
        assert params["type"] == "object", name
        assert params["additionalProperties"] is False, name
        assert '"title"' not in json.dumps(params), name


def test_save_answer_schema_is_narrowed_to_firm_config() -> None:
    props = definitions()["save_intake_answer"]["parameters"]["properties"]
    assert props["field_key"]["enum"] == [q.key for q in CONFIG.intake_questions]
    assert set(props["status"]["enum"]) == {
        "REPORTED_BY_CALLER",
        "CONFIRMED_BY_CALLER",
        "DECLINED_BY_CALLER",
        "PENDING_HUMAN_REVIEW",
    }  # VERIFIED_BY_SYSTEM and MISSING are not offered to the model
    assert definitions()["save_intake_answer"]["parameters"]["required"] == ["field_key", "status"]


def test_category_and_rule_enums_come_from_config() -> None:
    availability = definitions()["check_appointment_availability"]["parameters"]["properties"]
    codes = next(o for o in availability["matter_category"]["anyOf"] if o.get("type") == "string")
    assert codes["enum"] == [c.code for c in CONFIG.matter_categories]
    review = definitions()["mark_for_human_review"]["parameters"]["properties"]
    rules = next(o for o in review["rule_id"]["anyOf"] if o.get("type") == "string")
    assert rules["enum"] == ["imminent_deadline", "outside_intake_workflow"]


def test_system_prompt_states_boundaries_and_dates() -> None:
    text = prompts.system_prompt("Northstar Legal", date(2026, 10, 12), "Asia/Kolkata", CONFIG)
    assert "2026-10-12" in text and "tomorrow is 2026-10-13" in text
    assert "Never give legal advice" in text
    assert "verified=true" in text
    for q in CONFIG.intake_questions:
        assert f"- {q.key} [" in text
    assert "can't give legal advice" in prompts.greeting("Northstar Legal")
