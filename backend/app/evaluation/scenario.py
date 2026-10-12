"""Labelled evaluation scenarios (blueprint §13), validated strictly from fixtures/scenarios."""

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Track = Literal["system", "live"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ToolStep(Strict):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ScriptStep(Strict):
    """One scripted model response: text, a round of tool calls, or a provider outage."""

    say: str | None = None
    tools: tuple[ToolStep, ...] | None = None
    outage: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if sum(x is not None for x in (self.say, self.tools, self.outage)) != 1:
            raise ValueError("a script step needs exactly one of say / tools / outage")
        return self


class FieldExpect(Strict):
    equals: str | None = None
    contains: str | None = None
    one_of: tuple[str, ...] | None = Field(default=None, alias="in")
    equals_days_from_today: int | None = None
    status_in: tuple[str, ...] | None = None
    not_null: bool = False
    history_at_least: int = 0


class HandoffExpect(Strict):
    status: str | None = None  # escalation status after the handoff ran (PENDING / FAILED ...)
    delivered: bool | None = None  # reviewer alert (NEW_ESCALATION) exists
    fallback_notified: bool | None = None  # HANDOFF_FAILED_FALLBACK exists


class Expect(Strict):
    fields: dict[str, FieldExpect] = Field(default_factory=dict)
    escalation_rules: tuple[str, ...] | None = None  # exact set of CONFIGURED_RULE rule ids
    escalation_reasons: tuple[str, ...] | None = None  # exact multiset of reason codes
    appointments: int | None = None  # exact number of BOOKED appointments for the intake
    intake_status_in: tuple[str, ...] | None = None
    session_status_in: tuple[str, ...] | None = None
    no_intake: bool = False
    replies_must_not_contain: tuple[str, ...] = ()
    tool_errors_include: tuple[str, ...] = ()
    guard_blocks_at_least: int = 0
    workflows_requested: tuple[str, ...] = ()
    handoff: HandoffExpect | None = None


class Faults(Strict):
    calendar: str | None = None  # see app/workflows/faults.py
    notifier: str | None = None
    db_outage_tool: str | None = None  # this tool fails with a database error


class Scenario(Strict):
    id: str = Field(pattern=r"^[A-Z]\d{2}_[a-z0-9_]+$")
    category: str
    blueprint_case: str
    description: str
    critical: bool = False  # a configured critical escalation must be triggered
    tracks: tuple[Track, ...] = Field(min_length=1)
    caller_turns: tuple[str, ...] = Field(min_length=1)
    disconnect: bool = False  # the caller hangs up after the last turn
    scripted_model: tuple[ScriptStep, ...] = ()
    faults: Faults = Faults()
    durable_booking: bool = False  # book through the Temporal workflow
    expect: Expect

    @model_validator(mode="after")
    def _system_track_needs_a_script(self) -> Self:
        if "system" in self.tracks and not self.scripted_model:
            raise ValueError(f"{self.id}: the system track needs scripted_model steps")
        if (self.faults.calendar or self.faults.notifier) and "live" in self.tracks:
            raise ValueError(f"{self.id}: workflow faults are only evaluated on the system track")
        return self

    @property
    def needs_temporal(self) -> bool:
        return self.durable_booking or self.expect.handoff is not None


def load_scenarios(path: Path) -> list[Scenario]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    scenarios = [Scenario.model_validate(item) for item in raw]
    ids = [s.id for s in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("scenario ids must be unique")
    return scenarios
