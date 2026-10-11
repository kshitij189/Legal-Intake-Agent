"""Local speech-to-text: faster-whisper behind LiveKit's STT interface.

Non-streaming: LiveKit's VAD (Silero) segments the caller's speech, and each segment is transcribed
once. The model runs in a worker thread so the realtime loop is never blocked.
"""

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import Any, Literal

from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, LanguageCode, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer

from app.voice.audio import to_whisper_input

logger = logging.getLogger(__name__)

# Segments Whisper itself thinks are probably silence are dropped: on silence or noise Whisper
# tends to hallucinate short phrases ("Thank you.") that would otherwise become caller turns.
NO_SPEECH_THRESHOLD = 0.6


class WhisperSTT(stt.STT[Literal["metrics_collected"]]):
    def __init__(
        self,
        *,
        model_size: str,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
        download_root: Path | None = None,
        model: Any = None,
    ) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._download_root = download_root
        self._whisper = model  # injectable for tests
        self._lock = asyncio.Lock()  # one transcription at a time per model instance
        self.last_duration_ms: int | None = None

    @property
    def model(self) -> str:
        return self._model_size

    @property
    def provider(self) -> str:
        return "faster-whisper"

    def load(self) -> None:
        """Load the model eagerly (call once per worker process, before the first call)."""
        if self._whisper is None:
            from faster_whisper import WhisperModel  # heavy import, deferred

            self._whisper = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                download_root=str(self._download_root) if self._download_root else None,
            )

    def transcribe_samples(self, samples: Any, language: str) -> tuple[str, float]:
        """Blocking transcription of 16 kHz mono float32 samples -> (text, confidence)."""
        self.load()
        segments, _ = self._whisper.transcribe(
            samples,
            language=language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        kept = [s for s in segments if s.no_speech_prob < NO_SPEECH_THRESHOLD]
        text = " ".join(s.text.strip() for s in kept).strip()
        confidence = sum(math.exp(s.avg_logprob) for s in kept) / len(kept) if kept else 0.0
        return text, confidence

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        frame = rtc.combine_audio_frames(buffer) if isinstance(buffer, list) else buffer
        samples = to_whisper_input(frame.data, frame.sample_rate, frame.num_channels)
        lang = language if isinstance(language, str) else self._language
        started = time.perf_counter()
        try:
            async with self._lock:
                text, confidence = await asyncio.to_thread(self.transcribe_samples, samples, lang)
        except Exception as exc:
            logger.exception("stt_failed")
            raise APIConnectionError("local speech recognition failed") from exc
        self.last_duration_ms = round((time.perf_counter() - started) * 1000)
        # Latency only - transcripts contain personal data and are never logged.
        logger.info(
            "stt_transcribed stt_ms=%d audio_ms=%d",
            self.last_duration_ms,
            round(samples.size / 16),
            extra={
                "stt_ms": self.last_duration_ms,
                "audio_ms": round(samples.size / 16),
                "chars": len(text),
            },
        )
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(language=LanguageCode(lang), text=text, confidence=confidence)
            ],
        )
