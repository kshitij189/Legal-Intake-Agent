"""Deactivate the throwaway firms created by evaluation and demo runs (slugs `eval-*`, `dash-*`).

Their data stays for inspection; the outbox dispatcher skips inactive firms, so leftover workflow
requests are never started by scripts/temporal_worker.py. Runs created since Phase 8 retire their
firm automatically; this handles older ones.

Usage: .venv\\Scripts\\python scripts/retire_eval_firms.py [--dry-run]
"""

import argparse

from sqlalchemy import func, or_, select

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.models import Firm, WorkflowExecution
from app.models.enums import WorkflowStatus


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sessions = make_session_factory(make_engine(get_settings().database_url))
    with sessions() as s, s.begin():
        firms = s.scalars(
            select(Firm).where(
                Firm.is_active.is_(True), or_(Firm.slug.like("eval-%"), Firm.slug.like("dash-%"))
            )
        ).all()
        pending = s.scalar(
            select(func.count())
            .select_from(WorkflowExecution)
            .where(
                WorkflowExecution.firm_id.in_([f.id for f in firms]),
                WorkflowExecution.status == WorkflowStatus.REQUESTED,
            )
        )
        print(
            f"{len(firms)} active evaluation/demo firms, {pending} undispatched workflow requests"
        )
        if not args.dry_run:
            for firm in firms:
                firm.is_active = False
            print("deactivated" if firms else "nothing to do")


if __name__ == "__main__":
    main()
