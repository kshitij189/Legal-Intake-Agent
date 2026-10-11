"""Voice worker for the automated audio smoke test ONLY.

Identical to scripts/voice_worker.py except that replies come from a deterministic scripted model,
so the audio pipeline (LiveKit WebRTC -> VAD -> Whisper -> conversation -> Piper -> WebRTC) can be
verified without spending - or depending on - LLM quota. Never use this for real callers.
Started and stopped by scripts/voice_e2e_smoke.py.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from livekit.agents import AgentServer, JobContext, cli

from app.agent.llm_client import LLMResponse, ToolCall
from app.config import get_settings
from app.db.seed import DEMO_FIRM_TIMEZONE
from app.voice.session_manager import prewarm, run_voice_session

SMOKE_REPLY = (
    "Thank you. I have flagged the deadline for the team's prompt review. A member of the "
    "firm's team will look at your enquiry as soon as possible, and I can also note any other "
    "details you would like them to know before they contact you."
)


class SmokeScriptedLLM:
    """First caller turn: create the intake and save tomorrow's deadline, then reply. Later turns:
    a short fixed reply. Labelled provider 'smoke-scripted' in every trace."""

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        tool_rounds = sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
        if messages[-1]["role"] == "tool" or tool_rounds:
            return LLMResponse(
                content=SMOKE_REPLY if tool_rounds == 1 else "Noted, thank you.",
                provider="smoke-scripted",
            )
        tomorrow = datetime.now(UTC).astimezone(ZoneInfo(DEMO_FIRM_TIMEZONE)) + timedelta(days=1)
        return LLMResponse(
            content=None,
            provider="smoke-scripted",
            tool_calls=(
                ToolCall("smoke1", "create_draft_intake", "{}"),
                ToolCall(
                    "smoke2",
                    "save_intake_answer",
                    '{"field_key": "reported_deadline", "status": "REPORTED_BY_CALLER", '
                    f'"value": "{tomorrow.date().isoformat()}"}}',
                ),
            ),
        )


settings = get_settings()
server = AgentServer(
    setup_fnc=prewarm,
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret.get_secret_value(),
    num_idle_processes=settings.voice_idle_processes,
    initialize_process_timeout=180.0,
)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    await run_voice_session(ctx, llm_factory=SmokeScriptedLLM)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)
