"""Seed the fictional Northstar Legal firm, staff and a rolling test calendar (idempotent).

Usage: .venv\\Scripts\\python scripts/seed_demo_data.py
"""

from datetime import UTC, datetime

from app.config import get_settings
from app.db.seed import seed_demo_data
from app.db.session import make_engine, make_session_factory


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session, session.begin():
        result = seed_demo_data(session, slug=settings.demo_firm_slug, now=datetime.now(UTC))
    print(f"firm_id={result.firm_id}")
    for email, staff_id in result.staff_ids.items():
        print(f"staff {email} -> {staff_id}")
    print(f"new availability slots created: {result.slots_created}")


if __name__ == "__main__":
    main()
