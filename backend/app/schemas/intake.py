from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActorType, FieldStatus


class FieldEntry(BaseModel):
    """One intake field with its provenance. Stored inside `intake_records.fields`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str | None = None
    status: FieldStatus = FieldStatus.MISSING
    note: str | None = None
    source: ActorType | None = None
    updated_at: datetime | None = None
