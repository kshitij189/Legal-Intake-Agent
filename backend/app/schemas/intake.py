from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ActorType, FieldStatus

MAX_FIELD_HISTORY = 5


class PriorValue(BaseModel):
    """A value the field held before a correction (kept so reviewers see what changed)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str | None
    status: FieldStatus
    updated_at: datetime | None = None


class FieldEntry(BaseModel):
    """One intake field with its provenance. Stored inside `intake_records.fields`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str | None = None
    status: FieldStatus = FieldStatus.MISSING
    note: str | None = None
    source: ActorType | None = None
    updated_at: datetime | None = None
    # Most recent first, bounded by MAX_FIELD_HISTORY.
    history: tuple[PriorValue, ...] = Field(default=())
