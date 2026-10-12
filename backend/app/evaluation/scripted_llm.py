"""Deterministic model for the system track: plays a scenario's scripted steps in order.

Placeholders in tool arguments are resolved at run time:
  {{today+N}}         firm-local date N days from today (ISO)
  {{offered_slot:N}}  slot_id of the Nth slot in the latest availability result
"""

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from app.agent.llm_client import LLMResponse, LLMUnavailable, ToolCall
from app.evaluation.scenario import ScriptStep

PROVIDER = "scripted-eval"
_TODAY = re.compile(r"\{\{today([+-]\d+)?\}\}")
_SLOT = re.compile(r"\{\{offered_slot:(\d+)\}\}")


class ScriptExhausted(Exception):
    """The conversation asked for more model responses than the scenario scripts."""


class ScenarioLLM:
    def __init__(self, steps: Sequence[ScriptStep], firm_today: date) -> None:
        self._steps = list(steps)
        self._today = firm_today
        self._calls = 0

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        if not self._steps:
            raise ScriptExhausted(f"no scripted step for model call #{self._calls + 1}")
        self._calls += 1
        step = self._steps.pop(0)
        if step.outage is not None:
            raise LLMUnavailable(step.outage)
        if step.say is not None:
            return LLMResponse(content=step.say, provider=PROVIDER)
        assert step.tools is not None
        calls = tuple(
            ToolCall(
                id=f"eval_{self._calls}_{i}",
                name=tool.name,
                arguments=json.dumps(self._resolve(tool.args, messages)),
            )
            for i, tool in enumerate(step.tools)
        )
        return LLMResponse(content=None, tool_calls=calls, provider=PROVIDER)

    @property
    def remaining_steps(self) -> int:
        return len(self._steps)

    def _resolve(self, value: Any, messages: Sequence[Mapping[str, Any]]) -> Any:
        if isinstance(value, dict):
            return {k: self._resolve(v, messages) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v, messages) for v in value]
        if not isinstance(value, str):
            return value
        value = _TODAY.sub(
            lambda m: (self._today + timedelta(days=int(m.group(1) or 0))).isoformat(), value
        )
        slot = _SLOT.fullmatch(value)
        if slot:
            return latest_offered_slots(messages)[int(slot.group(1))]
        return value


def latest_offered_slots(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        try:
            data = json.loads(message["content"]).get("data") or {}
        except (ValueError, AttributeError):
            continue
        if "slots" in data:
            return [s["slot_id"] for s in data["slots"]]
    raise ScriptExhausted("no availability result to take an offered slot from")
