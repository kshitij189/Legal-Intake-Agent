"""Builds and wires a LiveKit voice session around one `IntakeConversation`.

Per worker process (`prewarm`): VAD, Whisper and Piper models are loaded once.
Per call (`run_voice_session`): a conversation, an AgentSession and its event handlers.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

import numpy as np
from livekit.agents import (
    AgentSession,
    AgentStateChangedEvent,
    CloseEvent,
    ConversationItemAddedEvent,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    llm,
)
from livekit.plugins import silero
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.conversation import IntakeConversation
from app.agent.llm_client import LLMClient, build_llm_client
from app.agent.voice_agent import ConversationOwnedLLM, IntakeVoiceAgent
from app.config import Settings, get_settings
from app.db.session import make_engine, make_session_factory
from app.models import Firm
from app.models.enums import SessionChannel
from app.voice.gpu import onnx_cuda_available, resolve_stt, resolve_tts_engine
from app.voice.stt import WhisperSTT
from app.voice.tts import PiperTTS
from app.voice.tts_kokoro import KokoroTTS, load_kokoro

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Future[None]] = set()


def load_voice_models(settings: Settings) -> dict[str, Any]:
    device, compute_type = resolve_stt(settings.stt_device, settings.stt_compute_type)
    stt = WhisperSTT(
        model_size=settings.stt_model,
        device=device,
        compute_type=compute_type,
        language=settings.stt_language,
        download_root=settings.models_dir / "whisper",
    )
    stt.load()
    engine = resolve_tts_engine(settings.tts_engine, settings.kokoro_min_gpu_mib)
    tts: PiperTTS | KokoroTTS
    if engine == "kokoro":
        kokoro = load_kokoro(
            settings.kokoro_model_path, settings.kokoro_voices_path, use_gpu=onnx_cuda_available()
        )
        tts = KokoroTTS(kokoro=kokoro, voice=settings.kokoro_voice)
    else:
        tts = PiperTTS(voice_path=settings.tts_voice_path)
    # Warm-up: the first CUDA call costs ~2 s; pay it here instead of on the caller's first turn.
    stt.transcribe_samples(np.zeros(16_000, dtype=np.float32), settings.stt_language)
    tts.synthesize_pcm("Hello.")
    logger.info(
        "voice_models_ready stt=%s/%s/%s tts=%s",
        settings.stt_model,
        device,
        compute_type,
        tts.model,
    )
    return {"vad": silero.VAD.load(), "stt": stt, "tts": tts}


def prewarm(proc: JobProcess) -> None:
    """Worker-process setup: models and the DB engine are created once, not per call."""
    settings = get_settings()
    proc.userdata.update(load_voice_models(settings))
    proc.userdata["sessions"] = make_session_factory(make_engine(settings.database_url))


def demo_firm_id(sessions: sessionmaker[Session], slug: str) -> uuid.UUID:
    with sessions() as s:
        return s.scalars(select(Firm.id).where(Firm.slug == slug)).one()


async def run_voice_session(
    ctx: JobContext,
    *,
    llm_factory: Callable[[], LLMClient] | None = None,
) -> IntakeVoiceAgent:
    settings = get_settings()
    userdata = ctx.proc.userdata
    sessions: sessionmaker[Session] = userdata["sessions"]
    firm_id = await asyncio.to_thread(demo_firm_id, sessions, settings.demo_firm_slug)

    conversation = IntakeConversation(
        llm=(llm_factory or (lambda: build_llm_client(settings)))(),
        sessions=sessions,
        firm_id=firm_id,
        channel=SessionChannel.VOICE,
    )
    agent = IntakeVoiceAgent(conversation)
    session: AgentSession[None] = AgentSession(
        stt=userdata["stt"],
        vad=userdata["vad"],
        tts=userdata["tts"],
        llm=ConversationOwnedLLM(),
        allow_interruptions=True,
    )
    wire_session_events(session, agent)
    await ctx.connect()
    await session.start(agent=agent, room=ctx.room)
    return agent


def wire_session_events(session: AgentSession[None], agent: IntakeVoiceAgent) -> None:
    conversation = agent.conversation

    @session.on("conversation_item_added")
    def _on_item(event: ConversationItemAddedEvent) -> None:
        item = event.item
        if isinstance(item, llm.ChatMessage) and item.role == "assistant" and item.interrupted:
            # The caller barged in: flag the transcript turn and tell the model.
            asyncio.get_event_loop().run_in_executor(None, conversation.mark_last_reply_interrupted)

    @session.on("agent_state_changed")
    def _on_state(event: AgentStateChangedEvent) -> None:
        # After an LLM outage the conversation ends itself; hang up once the apology is spoken.
        if conversation.ended and event.new_state == "listening":
            task = asyncio.ensure_future(session.aclose())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    @session.on("metrics_collected")
    def _on_metrics(event: MetricsCollectedEvent) -> None:
        metrics = event.metrics
        logger.info(
            "voice_metrics",
            extra={"metric": type(metrics).__name__, **_numeric_fields(metrics)},
        )

    @session.on("close")
    def _on_close(event: CloseEvent) -> None:
        # Caller hung up or the room closed: keep partial state (unfinished intake -> INCOMPLETE).
        if conversation.call_session_id is not None and not conversation.ended:
            asyncio.get_event_loop().run_in_executor(None, _end_quietly, conversation)


def _end_quietly(conversation: IntakeConversation) -> None:
    try:
        conversation.end()
    except Exception:
        logger.exception("voice_session_end_failed")


def _numeric_fields(metrics: Any) -> dict[str, float]:
    data = metrics.model_dump() if hasattr(metrics, "model_dump") else {}
    return {k: v for k, v in data.items() if isinstance(v, int | float) and not isinstance(v, bool)}
