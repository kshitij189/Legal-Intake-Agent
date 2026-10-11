"""Local text-to-speech: Piper behind LiveKit's TTS interface (non-streaming, per reply)."""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectionError, APIConnectOptions, tts

logger = logging.getLogger(__name__)


class PiperTTS(tts.TTS[Literal["metrics_collected"]]):
    def __init__(self, *, voice_path: Path | None = None, voice: Any = None) -> None:
        if voice is None:
            from piper import PiperVoice  # heavy import, deferred

            if voice_path is None or not voice_path.exists():
                raise FileNotFoundError(
                    f"Piper voice not found at {voice_path}; run scripts/download_voice_models.py"
                )
            voice = PiperVoice.load(str(voice_path))
        self._voice = voice
        self._voice_name = voice_path.stem if voice_path else "piper"
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=int(voice.config.sample_rate),
            num_channels=1,
        )
        self.last_duration_ms: int | None = None

    @property
    def model(self) -> str:
        return self._voice_name

    @property
    def provider(self) -> str:
        return "piper"

    def synthesize_pcm(self, text: str) -> bytes:
        """Blocking synthesis -> 16-bit mono PCM at `self.sample_rate`."""
        return b"".join(chunk.audio_int16_bytes for chunk in self._voice.synthesize(text))

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "PiperStream":
        return PiperStream(tts=self, input_text=text, conn_options=conn_options)


class PiperStream(tts.ChunkedStream):
    def __init__(self, *, tts: PiperTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._piper = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=uuid.uuid4().hex,
            sample_rate=self._piper.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        started = time.perf_counter()
        try:
            pcm = await asyncio.to_thread(self._piper.synthesize_pcm, self._input_text)
        except Exception as exc:
            logger.exception("tts_failed")
            raise APIConnectionError("local speech synthesis failed") from exc
        self._piper.last_duration_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "tts_synthesized tts_ms=%d",
            self._piper.last_duration_ms,
            extra={
                "tts_ms": self._piper.last_duration_ms,
                "audio_ms": round(len(pcm) / 2 / self._piper.sample_rate * 1000),
            },
        )
        output_emitter.push(pcm)
        output_emitter.flush()
