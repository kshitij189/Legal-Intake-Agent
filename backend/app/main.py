"""FastAPI application.

Phase 4 scope: the local voice demo only - a page that joins a LiveKit room with the microphone and
an endpoint issuing a join token for a fresh room per call. It performs no business writes. The
full REST surface (blueprint §12) and staff authentication arrive in Phase 7. Bind to 127.0.0.1:
the token endpoint is unauthenticated by design for the local demo.
"""

import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from livekit import api
from pydantic import BaseModel

from app.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent / "web"
TOKEN_TTL = timedelta(minutes=30)

app = FastAPI(title="Legal Intake Agent", docs_url=None, redoc_url=None)


class VoiceToken(BaseModel):
    url: str
    token: str
    room: str
    identity: str


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/voice")


@app.get("/voice", include_in_schema=False)
def voice_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "voice.html")


@app.post("/api/v1/voice/token")
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
