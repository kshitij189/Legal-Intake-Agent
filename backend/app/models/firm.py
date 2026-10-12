import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_type
from app.models.enums import StaffRole


class Firm(Base):
    __tablename__ = "firms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(default=True)
    # Validated by app.schemas.firm_config.FirmConfig: questionnaire, escalation rules, scheduling.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StaffMember(Base):
    __tablename__ = "staff_members"
    __table_args__ = (
        UniqueConstraint("firm_id", "email"),
        # Target for composite (firm_id, staff_id) foreign keys: child rows stay in one tenant.
        UniqueConstraint("firm_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[StaffRole] = mapped_column(enum_type(StaffRole, "staff_role"))
    practice_areas: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    is_active: Mapped[bool] = mapped_column(default=True)
    # Dashboard login (Phase 7). scrypt hash, see app.domain.staff_auth_service; NULL = no login.
    password_hash: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffSession(Base):
    """A dashboard login. Only a SHA-256 of the bearer token is stored."""

    __tablename__ = "staff_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["firm_id", "staff_id"], ["staff_members.firm_id", "staff_members.id"]
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firms.id"))
    staff_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
