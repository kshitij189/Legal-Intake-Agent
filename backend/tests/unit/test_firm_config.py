import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.db.seed import DEMO_FIRM_CONFIG
from app.schemas.firm_config import FirmConfig


def _config() -> dict[str, Any]:
    return copy.deepcopy(DEMO_FIRM_CONFIG)


def test_demo_config_is_valid() -> None:
    config = FirmConfig.model_validate(DEMO_FIRM_CONFIG)
    assert [c.code for c in config.matter_categories] == [
        "EMPLOYMENT",
        "FAMILY",
        "PROPERTY",
        "CONSUMER",
        "OTHER",
    ]
    assert config.rule("imminent_deadline") is not None
    assert config.question("does_not_exist") is None


def test_matter_category_question_is_required() -> None:
    raw = _config()
    raw["intake_questions"] = [q for q in raw["intake_questions"] if q["key"] != "matter_category"]
    raw["escalation_rules"] = []
    with pytest.raises(ValidationError, match="matter_category"):
        FirmConfig.model_validate(raw)


def test_rule_must_reference_existing_field() -> None:
    raw = _config()
    raw["escalation_rules"][0]["condition"]["field_key"] = "nope"
    with pytest.raises(ValidationError, match="unknown field"):
        FirmConfig.model_validate(raw)


def test_deadline_rule_requires_date_field() -> None:
    raw = _config()
    raw["escalation_rules"][0]["condition"]["field_key"] = "matter_summary"
    with pytest.raises(ValidationError, match="DATE"):
        FirmConfig.model_validate(raw)


def test_value_rule_codes_must_exist() -> None:
    raw = _config()
    raw["escalation_rules"][1]["condition"]["values"] = ["CRIMINAL"]
    with pytest.raises(ValidationError, match="unknown codes"):
        FirmConfig.model_validate(raw)


def test_duplicate_question_keys_rejected() -> None:
    raw = _config()
    raw["intake_questions"].append(dict(raw["intake_questions"][0]))
    with pytest.raises(ValidationError, match="unique"):
        FirmConfig.model_validate(raw)


def test_choice_questions_need_choices_and_others_must_not_have_them() -> None:
    raw = _config()
    raw["intake_questions"][3]["choices"] = []
    with pytest.raises(ValidationError, match="at least one choice"):
        FirmConfig.model_validate(raw)

    raw = _config()
    raw["intake_questions"][0]["choices"] = [{"code": "X", "label": "x"}]
    with pytest.raises(ValidationError, match="only CHOICE"):
        FirmConfig.model_validate(raw)


def test_unknown_keys_are_rejected() -> None:
    raw = _config()
    raw["llm_may_decide_urgency"] = True
    with pytest.raises(ValidationError):
        FirmConfig.model_validate(raw)
