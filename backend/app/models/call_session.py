import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_type
from app.models.enums import CallSessionStatus, SessionChannel


class CallSession(Base):
    """One conversation (text now, voice from Phase 4). May produce one intake record."""

    __tablename__ = "call_sessions"
    __table_args__ = (
        ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        ForeignKeyConstraint(
            ["firm_id", "intake_id"], ["intake_records.firm_id", "intake_records.id"]
        ),
        UniqueConstraint("firm_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID]
    intake_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    channel: Mapped[SessionChannel] = mapped_column(enum_type(SessionChannel, "session_channel"))
    status: Mapped[CallSessionStatus] = mapped_column(
        enum_type(CallSessionStatus, "call_session_status"), default=CallSessionStatus.ACTIVE
    )
    end_reason: Mapped[str | None] = mapped_column(String(200))
    # What the caller was told and what is retained, e.g.
    # {"transcript_retained": true, "disclosure": "...", "audio_recorded": false}
    consent: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
