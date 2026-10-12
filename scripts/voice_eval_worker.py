"""Voice worker for the audio evaluation track ONLY (started by scripts/run_evaluations.py).

Same pipeline as scripts/voice_worker.py (LiveKit WebRTC -> VAD -> Whisper -> conversation ->
TTS -> WebRTC). The model is a fixed acknowledgement by default, so speech recognition, synthesis,
latency and barge-in are measured without spending - or depending on - LLM quota. Set
VOICE_EVAL_LLM=live to use the real model chain instead. Never use this for real callers.
"""

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from livekit.agents import AgentServer, JobContext, cli

from app.agent.llm_client import LLMClient, LLMResponse, build_llm_client
from app.config import get_settings
from app.voice.session_manager import prewarm, run_voice_session

# Long enough (~6 s of speech) for the barge-in scenario to talk over it.
ACK_REPLY = (
    "Thank you, I have noted that for the firm's team. They will review everything you have "
    "told me before anyone contacts you. Please go on whenever you are ready."
)


class AckLLM:
    """Replies with ACK_REPLY to every caller turn and never calls tools."""

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        return LLMResponse(content=ACK_REPLY, provider="eval-ack")


settings = get_settings()
server = AgentServer(
    setup_fnc=prewarm,
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret.get_secret_value(),
    num_idle_processes=settings.voice_idle_processes,
    initialize_process_timeout=180.0,
)


def make_llm() -> LLMClient:
    return build_llm_client(settings) if os.environ.get("VOICE_EVAL_LLM") == "live" else AckLLM()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    await run_voice_session(ctx, llm_factory=make_llm)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)
