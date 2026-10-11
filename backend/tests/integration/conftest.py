"""Integration fixtures against a real PostgreSQL test database.

The schema is rebuilt from the Alembic migrations once per run. Each test runs inside a
transaction that is rolled back; `committed_demo` is for tests that need real commits
(concurrency, fresh-session verification) and truncates afterwards.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.seed import SeedResult, seed_demo_data, seed_firm, seed_slots, seed_staff
from app.db.session import make_engine, make_session_factory
from app.domain.actors import Actor
from tests.helpers import DEMO_SLUG, NOW

BACKEND_DIR = Path(__file__).resolve().parents[2]
TABLES = (
    "audit_events, call_sessions, appointments, escalations, intake_records, availability_slots, "
    "staff_members, firms"
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
def test_db_url() -> str:
    url = get_settings().test_database_url
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set (see .env.example)")
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.fail(f"refusing to run destructive tests against database '{database}'")
    return url


@pytest.fixture(scope="session")
def engine(test_db_url: str) -> Iterator[Engine]:
    engine = make_engine(test_db_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(test_db_url), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@dataclass(frozen=True)
class SeededFirm:
    firm_id: uuid.UUID
    staff_ids: dict[str, uuid.UUID]

    def staff(self, prefix: str) -> uuid.UUID:
        return next(sid for email, sid in self.staff_ids.items() if email.startswith(prefix))

    def actor(self, prefix: str) -> Actor:
        return Actor.staff(self.staff(prefix))

    @property
    def coordinator(self) -> Actor:
        return self.actor("sana.kapoor")


@pytest.fixture
def demo(session: Session) -> SeededFirm:
    result: SeedResult = seed_demo_data(session, slug=DEMO_SLUG, now=NOW)
    return SeededFirm(result.firm_id, result.staff_ids)


@pytest.fixture
def other_firm(session: Session) -> SeededFirm:
    """A second tenant with identical configuration and staff emails."""
    firm_id = seed_firm(session, slug="other-firm", name="Other Firm (fictional)")
    staff_ids = seed_staff(session, firm_id)
    lawyer = staff_ids["aarav.mehta@northstar-legal.example"]
    seed_slots(
        session,
        firm_id=firm_id,
        lawyer_ids=[lawyer],
        timezone="Asia/Kolkata",
        consultation_minutes=30,
        now=NOW,
    )
    session.flush()
    return SeededFirm(firm_id, staff_ids)


@pytest.fixture
def committed_demo(engine: Engine) -> Iterator[tuple[sessionmaker[Session], SeededFirm]]:
    factory = make_session_factory(engine)
    with factory() as s, s.begin():
        result = seed_demo_data(s, slug=DEMO_SLUG, now=NOW)
    yield factory, SeededFirm(result.firm_id, result.staff_ids)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
