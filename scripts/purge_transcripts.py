"""Retention: delete transcript turns older than each active firm's
`privacy.transcript_retention_days` (default 90). Intakes, escalations and the audit trail are kept.
Schedule it daily (e.g. Windows Task Scheduler or cron).

Usage: .venv\\Scripts\\python scripts/purge_transcripts.py
"""

from sqlalchemy import select

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.domain.actors import Actor
from app.domain.transcript_service import purge_expired_turns
from app.models import Firm


def main() -> None:
    sessions = make_session_factory(make_engine(get_settings().database_url))
    with sessions() as s:
        firms = s.scalars(select(Firm.id).where(Firm.is_active.is_(True))).all()
    for firm_id in firms:
        with sessions() as s, s.begin():
            deleted = purge_expired_turns(s, firm_id=firm_id, actor=Actor.system("retention"))
        print(f"firm {firm_id}: {deleted} transcript turns deleted")


if __name__ == "__main__":
    main()
