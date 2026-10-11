"""LiveKit voice worker for the intake agent.

Usage (LiveKit + Postgres running via docker compose):
    .venv\\Scripts\\python scripts/voice_worker.py dev       # join rooms created by the web page
    .venv\\Scripts\\python scripts/voice_worker.py console   # talk through this computer's mic

Uses the real LLM chain (Gemini, fallback Groq), local Whisper STT and local Piper TTS.
"""

import logging

from livekit.agents import AgentServer, JobContext, cli

from app.config import get_settings
from app.voice.session_manager import prewarm, run_voice_session

settings = get_settings()
server = AgentServer(
    setup_fnc=prewarm,
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret.get_secret_value(),
    initialize_process_timeout=120.0,  # loading Whisper + Piper on CPU takes a while
)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    await run_voice_session(ctx)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)
