import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_type
from app.models.enums import AppointmentStatus, SlotStatus


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["firm_id", "staff_id"], ["staff_members.firm_id", "staff_members.id"]
        ),
        UniqueConstraint("staff_id", "starts_at"),
        UniqueConstraint("firm_id", "id"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        Index("ix_availability_slots_firm_status_start", "firm_id", "status", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID]
    staff_id: Mapped[uuid.UUID]
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[SlotStatus] = mapped_column(
        enum_type(SlotStatus, "slot_status"), default=SlotStatus.AVAILABLE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        ForeignKeyConstraint(["firm_id"], ["firms.id"]),
        ForeignKeyConstraint(
            ["firm_id", "intake_id"], ["intake_records.firm_id", "intake_records.id"]
        ),
        ForeignKeyConstraint(
            ["firm_id", "slot_id"], ["availability_slots.firm_id", "availability_slots.id"]
        ),
        ForeignKeyConstraint(
            ["firm_id", "staff_id"], ["staff_members.firm_id", "staff_members.id"]
        ),
        # A retried request with the same key must never create a second appointment.
        UniqueConstraint("firm_id", "idempotency_key"),
        # Backstops for the service-level checks: one live booking per slot and per intake.
        Index(
            "uq_appointments_slot_booked",
            "slot_id",
            unique=True,
            postgresql_where=text("status = 'BOOKED'"),
        ),
        Index(
            "uq_appointments_intake_booked",
            "intake_id",
            unique=True,
            postgresql_where=text("status = 'BOOKED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID]
    intake_id: Mapped[uuid.UUID]
    slot_id: Mapped[uuid.UUID]
    staff_id: Mapped[uuid.UUID]
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AppointmentStatus] = mapped_column(
        enum_type(AppointmentStatus, "appointment_status"), default=AppointmentStatus.BOOKED
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
