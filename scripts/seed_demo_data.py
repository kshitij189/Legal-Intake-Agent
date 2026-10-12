"""Seed the fictional Northstar Legal firm, staff and a rolling test calendar (idempotent).

If DEMO_STAFF_PASSWORD is set in .env, every fictional staff member gets that dashboard password
(Phase 7). It is never stored in source control.

Usage: .venv\\Scripts\\python scripts/seed_demo_data.py
"""

from datetime import UTC, datetime

from app.config import get_settings
from app.db.seed import seed_demo_data
from app.db.session import make_engine, make_session_factory
from app.domain.staff_auth_service import set_password


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session, session.begin():
        result = seed_demo_data(session, slug=settings.demo_firm_slug, now=datetime.now(UTC))
        if settings.demo_staff_password is not None:
            for staff_id in result.staff_ids.values():
                set_password(
                    session,
                    firm_id=result.firm_id,
                    staff_id=staff_id,
                    password=settings.demo_staff_password.get_secret_value(),
                )
    print(f"firm_id={result.firm_id}")
    for email, staff_id in result.staff_ids.items():
        print(f"staff {email} -> {staff_id}")
    print(f"new availability slots created: {result.slots_created}")
    print(
        "dashboard passwords: "
        + ("set from DEMO_STAFF_PASSWORD" if settings.demo_staff_password else "not set")
    )


if __name__ == "__main__":
    main()
