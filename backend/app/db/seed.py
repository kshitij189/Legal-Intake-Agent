"""Fictional demo data: Northstar Legal, its staff, intake policy and a rolling test calendar.

All people, emails and policies are invented. Seeding is idempotent (upserts keyed on natural keys).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AvailabilitySlot, Firm, StaffMember
from app.models.enums import SlotStatus, StaffRole
from app.schemas.firm_config import FirmConfig

DEMO_FIRM_NAME = "Northstar Legal"
DEMO_FIRM_TIMEZONE = "Asia/Kolkata"

DEMO_FIRM_CONFIG: dict[str, Any] = {
    "intake_questions": [
        {"key": "caller_name", "prompt": "What is your full name?", "type": "TEXT"},
        {"key": "phone", "prompt": "What phone number can the firm reach you on?", "type": "PHONE"},
        {
            "key": "email",
            "prompt": "Is there an email address you'd like to share?",
            "type": "EMAIL",
            "required": False,
        },
        {
            "key": "preferred_contact_method",
            "prompt": "Would you prefer to be contacted by phone or email?",
            "type": "CHOICE",
            "choices": [{"code": "PHONE", "label": "Phone"}, {"code": "EMAIL", "label": "Email"}],
        },
        {
            "key": "matter_category",
            "prompt": "Which area does your enquiry relate to?",
            "type": "CHOICE",
            "choices": [
                {"code": "EMPLOYMENT", "label": "Employment"},
                {"code": "FAMILY", "label": "Family"},
                {"code": "PROPERTY", "label": "Property"},
                {"code": "CONSUMER", "label": "Consumer"},
                {"code": "OTHER", "label": "Something else"},
            ],
        },
        {
            "key": "matter_summary",
            "prompt": "Could you briefly describe what happened?",
            "type": "TEXT",
        },
        {
            "key": "jurisdiction",
            "prompt": "In which city or state did this happen?",
            "type": "TEXT",
            "required": False,
        },
        {
            "key": "reported_deadline",
            "prompt": "Is there any deadline or date mentioned in a notice or document?",
            "type": "DATE",
            "required": False,
        },
    ],
    "escalation_rules": [
        {
            "id": "imminent_deadline",
            "description": "Caller reported a deadline within 3 days (fictional firm policy).",
            "priority": "URGENT",
            "condition": {
                "kind": "deadline_within_days",
                "field_key": "reported_deadline",
                "days": 3,
            },
        },
        {
            "id": "outside_intake_workflow",
            "description": "Matter category is outside the firm's configured intake workflow.",
            "priority": "HIGH",
            "condition": {
                "kind": "field_value_in",
                "field_key": "matter_category",
                "values": ["OTHER"],
            },
        },
    ],
    "scheduling": {"consultation_minutes": 30, "min_notice_hours": 2, "max_days_ahead": 14},
}


@dataclass(frozen=True)
class StaffSeed:
    full_name: str
    email: str
    role: StaffRole
    practice_areas: tuple[str, ...] = ()


DEMO_STAFF = (
    StaffSeed(
        "Aarav Mehta", "aarav.mehta@northstar-legal.example", StaffRole.LAWYER, ("EMPLOYMENT",)
    ),
    StaffSeed(
        "Meera Iyer", "meera.iyer@northstar-legal.example", StaffRole.LAWYER, ("FAMILY", "CONSUMER")
    ),
    StaffSeed(
        "Daniel Brooks",
        "daniel.brooks@northstar-legal.example",
        StaffRole.LAWYER,
        ("PROPERTY", "EMPLOYMENT"),
    ),
    StaffSeed("Sana Kapoor", "sana.kapoor@northstar-legal.example", StaffRole.INTAKE_COORDINATOR),
    StaffSeed("Rohan Verma", "rohan.verma@northstar-legal.example", StaffRole.ADMINISTRATOR),
)

SLOT_TIMES_LOCAL = (time(10, 0), time(11, 0), time(14, 0), time(15, 0))
SLOT_BUSINESS_DAYS = 10


@dataclass(frozen=True)
class SeedResult:
    firm_id: uuid.UUID
    staff_ids: dict[str, uuid.UUID]  # by email
    slots_created: int


def seed_firm(
    session: Session,
    *,
    slug: str,
    name: str = DEMO_FIRM_NAME,
    timezone: str = DEMO_FIRM_TIMEZONE,
    config: dict[str, Any] | None = None,
) -> uuid.UUID:
    config = FirmConfig.model_validate(config or DEMO_FIRM_CONFIG).model_dump(mode="json")
    stmt = insert(Firm).values(
        id=uuid.uuid4(), slug=slug, name=name, timezone=timezone, is_active=True, config=config
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Firm.slug],
        set_={"name": name, "timezone": timezone, "config": config},
    )
    session.execute(stmt)
    firm_id = session.scalar(select(Firm.id).where(Firm.slug == slug))
    assert firm_id is not None
    return firm_id


def seed_staff(
    session: Session, firm_id: uuid.UUID, staff: tuple[StaffSeed, ...] = DEMO_STAFF
) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    for member in staff:
        upsert = (
            insert(StaffMember)
            .values(
                id=uuid.uuid4(),
                firm_id=firm_id,
                full_name=member.full_name,
                email=member.email,
                role=member.role,
                practice_areas=list(member.practice_areas),
                is_active=True,
            )
            .on_conflict_do_update(
                index_elements=[StaffMember.firm_id, StaffMember.email],
                set_={
                    "full_name": member.full_name,
                    "role": member.role,
                    "practice_areas": list(member.practice_areas),
                },
            )
            .returning(StaffMember.id)
        )
        staff_id = session.scalar(upsert)
        assert staff_id is not None
        ids[member.email] = staff_id
    return ids


def seed_slots(
    session: Session,
    *,
    firm_id: uuid.UUID,
    lawyer_ids: list[uuid.UUID],
    timezone: str,
    consultation_minutes: int,
    now: datetime,
) -> int:
    """Create slots for the next business days. Existing slots (same lawyer/start) are kept."""
    tz = ZoneInfo(timezone)
    day = now.astimezone(tz).date()
    created, business_days = 0, 0
    while business_days < SLOT_BUSINESS_DAYS:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        business_days += 1
        for staff_id in lawyer_ids:
            for start_local in SLOT_TIMES_LOCAL:
                starts_at = datetime.combine(day, start_local, tzinfo=tz)
                stmt = (
                    insert(AvailabilitySlot)
                    .values(
                        id=uuid.uuid4(),
                        firm_id=firm_id,
                        staff_id=staff_id,
                        starts_at=starts_at,
                        ends_at=starts_at + timedelta(minutes=consultation_minutes),
                        status=SlotStatus.AVAILABLE,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[AvailabilitySlot.staff_id, AvailabilitySlot.starts_at]
                    )
                    .returning(AvailabilitySlot.id)
                )
                if session.scalar(stmt) is not None:
                    created += 1
    return created


def seed_demo_data(session: Session, *, slug: str, now: datetime) -> SeedResult:
    firm_id = seed_firm(session, slug=slug)
    staff_ids = seed_staff(session, firm_id)
    lawyers = [staff_ids[s.email] for s in DEMO_STAFF if s.role is StaffRole.LAWYER]
    minutes = DEMO_FIRM_CONFIG["scheduling"]["consultation_minutes"]
    created = seed_slots(
        session,
        firm_id=firm_id,
        lawyer_ids=lawyers,
        timezone=DEMO_FIRM_TIMEZONE,
        consultation_minutes=minutes,
        now=now,
    )
    session.flush()
    return SeedResult(firm_id=firm_id, staff_ids=staff_ids, slots_created=created)
