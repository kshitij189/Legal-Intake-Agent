import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_type
from app.models.enums import EscalationReason, EscalationStatus, Priority


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (
        ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        ForeignKeyConstraint(
            ["firm_id", "intake_id"], ["intake_records.firm_id", "intake_records.id"]
        ),
        ForeignKeyConstraint(
            ["firm_id", "assigned_staff_id"], ["staff_members.firm_id", "staff_members.id"]
        ),
        UniqueConstraint("firm_id", "idempotency_key"),
        # One unresolved escalation per intake and configured rule.
        Index(
            "uq_escalations_open_rule",
            "intake_id",
            "rule_id",
            unique=True,
            postgresql_where=text("status <> 'RESOLVED' AND rule_id IS NOT NULL"),
        ),
        Index("ix_escalations_firm_status", "firm_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID]
    intake_id: Mapped[uuid.UUID]
    reason_code: Mapped[EscalationReason] = mapped_column(
        enum_type(EscalationReason, "escalation_reason")
    )
    rule_id: Mapped[str | None] = mapped_column(String(64))
    reason_text: Mapped[str] = mapped_column(Text)
    priority: Mapped[Priority] = mapped_column(enum_type(Priority, "priority"))
    status: Mapped[EscalationStatus] = mapped_column(
        enum_type(EscalationStatus, "escalation_status"), default=EscalationStatus.PENDING
    )
    assigned_staff_id: Mapped[uuid.UUID | None]
    resolution_note: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
