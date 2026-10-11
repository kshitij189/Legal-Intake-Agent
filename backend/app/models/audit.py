import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_type
from app.models.enums import ActorType, AuditOutcome


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "firm_id", "entity_type", "entity_id", "seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Strict insertion order; timestamps can tie for events written in one transaction.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    actor_type: Mapped[ActorType] = mapped_column(enum_type(ActorType, "actor_type"))
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID]
    outcome: Mapped[AuditOutcome] = mapped_column(enum_type(AuditOutcome, "audit_outcome"))
    # Identifiers and status changes only - never caller contact details or free-text statements.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
