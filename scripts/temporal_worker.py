"""Temporal worker for durable intake work (handoff, booking, post-call) + outbox dispatcher.

Usage: .venv\\Scripts\\python scripts/temporal_worker.py
Requires `docker compose up -d postgres temporal`. Temporal UI: http://127.0.0.1:8233
"""

import asyncio

from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.observability.logging import configure_logging
from app.workflows.worker import run_worker_forever


def main() -> None:
    configure_logging()
    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    asyncio.run(run_worker_forever(settings, sessions))


if __name__ == "__main__":
    main()
