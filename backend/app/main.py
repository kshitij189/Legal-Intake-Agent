"""FastAPI application.

- Phase 4: the local voice demo - a page that joins a LiveKit room with the microphone, and an
  endpoint issuing a join token for a fresh room per call (unauthenticated by design; no writes).
- Phase 7: the staff API under /api/v1 (app/api/routes.py), session-authenticated, used by the
  Next.js review dashboard in frontend/.

Local demo only: bind to 127.0.0.1.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from livekit import api
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from app.api import errors, middleware, routes
from app.api.throttle import LoginThrottle
from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.observability.logging import configure_logging
from app.workflows.client import signal_handoff_status_changed

STATIC_DIR = Path(__file__).resolve().parent / "web"
TOKEN_TTL = timedelta(minutes=30)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Only when the server runs: importing this module (e.g. for voice_token) must not
    # reconfigure the importer's logging.
    configure_logging()
    yield


def create_app(
    sessions: sessionmaker[Session] | None = None,
    handoff_signal: Callable[[uuid.UUID], object] | None = None,
) -> FastAPI:
    """`sessions` and `handoff_signal` are injectable for tests."""
    settings = get_settings()
    application = FastAPI(
        title="Legal Intake Agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    application.state.sessions = sessions or make_session_factory(
        make_engine(settings.database_url)
    )
    if handoff_signal is None:

        def handoff_signal(escalation_id: uuid.UUID) -> object:
            if not settings.handoff_signal_enabled:
                return False
            return signal_handoff_status_changed(settings, escalation_id)

    application.state.handoff_signal = handoff_signal
    application.state.login_throttle = LoginThrottle()
    errors.install(application)
    middleware.install(application)
    application.include_router(routes.router)
    application.add_api_route("/", index, include_in_schema=False)
    application.add_api_route("/voice", voice_page, include_in_schema=False)
    application.add_api_route("/api/v1/voice/token", voice_token, methods=["POST"])
    return application


class VoiceToken(BaseModel):
    url: str
    token: str
    room: str
    identity: str


def index() -> RedirectResponse:
    return RedirectResponse("/voice")


def voice_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "voice.html")


def voice_token() -> VoiceToken:
    """Token for one caller in a brand-new room; the voice worker is dispatched automatically."""
    settings = get_settings()
    room = f"intake-{uuid.uuid4().hex[:12]}"
    identity = f"caller-{uuid.uuid4().hex[:8]}"
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret.get_secret_value())
        .with_identity(identity)
        .with_ttl(TOKEN_TTL)
        .with_grants(
            api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
        )
        .to_jwt()
    )
    return VoiceToken(url=settings.livekit_url, token=token, room=room, identity=identity)


app = create_app()
