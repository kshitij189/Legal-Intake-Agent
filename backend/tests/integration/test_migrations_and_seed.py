from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.db.base import Base
from app.db.seed import DEMO_STAFF, SLOT_BUSINESS_DAYS, SLOT_TIMES_LOCAL, seed_demo_data
from app.domain.firm_config_service import load_config
from app.models import AvailabilitySlot, Firm, StaffMember
from app.models.enums import StaffRole
from tests.helpers import DEMO_SLUG, NOW
from tests.integration.conftest import alembic_config


def test_migrations_roundtrip_and_match_models(engine: Engine, test_db_url: str) -> None:
    cfg = alembic_config(test_db_url)
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename <> 'alembic_version'"
            )
        ).scalar_one()
        assert tables == 0
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == [], f"models drifted from migrations: {diff}"
        assert conn.execute(text("SELECT nextval('intake_reference_seq')")).scalar_one() >= 1001


def test_seed_is_idempotent_and_valid(session: Session) -> None:
    first = seed_demo_data(session, slug=DEMO_SLUG, now=NOW)
    second = seed_demo_data(session, slug=DEMO_SLUG, now=NOW)

    lawyers = sum(1 for s in DEMO_STAFF if s.role is StaffRole.LAWYER)
    assert lawyers == 3
    assert first.slots_created == lawyers * SLOT_BUSINESS_DAYS * len(SLOT_TIMES_LOCAL)
    assert second.slots_created == 0
    assert first.firm_id == second.firm_id
    assert first.staff_ids == second.staff_ids

    assert session.scalar(select(func.count()).select_from(Firm)) == 1
    assert session.scalar(select(func.count()).select_from(StaffMember)) == len(DEMO_STAFF)
    assert session.scalar(select(func.count()).select_from(AvailabilitySlot)) == (
        first.slots_created
    )
    firm = session.get_one(Firm, first.firm_id)
    load_config(firm)  # stored config passes validation

    weekend = session.scalar(
        select(func.count())
        .select_from(AvailabilitySlot)
        .where(
            func.extract("isodow", func.timezone("Asia/Kolkata", AvailabilitySlot.starts_at)) >= 6
        )
    )
    assert weekend == 0
