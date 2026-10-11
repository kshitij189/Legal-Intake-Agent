import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Sequence, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_type
from app.models.enums import IntakeStatus

# Human-readable references (INT-1001, INT-1002, ...). Created in the initial migration.
intake_reference_seq = Sequence("intake_reference_seq", start=1001, metadata=Base.metadata)


class IntakeRecord(Base):
    __tablename__ = "intake_records"
    __table_args__ = (UniqueConstraint("firm_id", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id"), index=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[IntakeStatus] = mapped_column(enum_type(IntakeStatus, "intake_status"))
    # {field_key: FieldEntry} - see app.schemas.intake.FieldEntry. Keys come from the firm config.
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    # Unanswered questions at finalization: {"missing", "declined", "needs_review": [keys]}
    outstanding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
