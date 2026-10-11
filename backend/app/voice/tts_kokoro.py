"""Local text-to-speech: Kokoro-82M (Apache-2.0 weights) via kokoro-onnx, behind LiveKit's TTS.

Uses the CUDA execution provider when available; on CPU it works but is ~11x slower than Piper
(measured 2026-10-12), so `TTS_ENGINE=auto` only picks Kokoro when the GPU path is usable.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import numpy as np
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectionError, APIConnectOptions, tts

from app.voice.audio import float_to_pcm16
from app.voice.gpu import register_cuda_dlls

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24_000


def load_kokoro(model_path: Path, voices_path: Path, use_gpu: bool) -> Any:
    for path in (model_path, voices_path):
        if not path.exists():
            raise FileNotFoundError(
                f"Kokoro file not found at {path}; run scripts/download_voice_models.py"
            )
    register_cuda_dlls()
    import onnxruntime
    from kokoro_onnx import Kokoro  # heavy import, deferred

    providers = (
        ["CUDAExecutionProvider", "CPUExecutionProvider"] if use_gpu else ["CPUExecutionProvider"]
    )
    session = onnxruntime.InferenceSession(str(model_path), providers=providers)
    if use_gpu and "CUDAExecutionProvider" not in session.get_providers():
        logger.warning("kokoro_gpu_unavailable_using_cpu")
    return Kokoro.from_session(session, str(voices_path))


class KokoroTTS(tts.TTS[Literal["metrics_collected"]]):
    def __init__(self, *, kokoro: Any, voice: str = "af_heart", lang: str = "en-us") -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=KOKORO_SAMPLE_RATE,
            num_channels=1,
        )
        self._kokoro = kokoro
        self._voice = voice
        self._lang = lang
        self.last_duration_ms: int | None = None

    @property
    def model(self) -> str:
        return f"kokoro-v1.0:{self._voice}"

    @property
    def provider(self) -> str:
        return "kokoro-onnx"

    def synthesize_pcm(self, text: str) -> bytes:
        """Blocking synthesis -> 16-bit mono PCM at 24 kHz."""
        samples, rate = self._kokoro.create(text, voice=self._voice, lang=self._lang)
        if rate != KOKORO_SAMPLE_RATE:
            raise ValueError(f"unexpected Kokoro sample rate {rate}")
        return float_to_pcm16(np.asarray(samples, dtype=np.float32))

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "KokoroStream":
        return KokoroStream(tts=self, input_text=text, conn_options=conn_options)


class KokoroStream(tts.ChunkedStream):
    def __init__(self, *, tts: KokoroTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._kokoro_tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=uuid.uuid4().hex,
            sample_rate=KOKORO_SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )
        started = time.perf_counter()
        try:
            pcm = await asyncio.to_thread(self._kokoro_tts.synthesize_pcm, self._input_text)
        except Exception as exc:
            logger.exception("tts_failed")
            raise APIConnectionError("local speech synthesis failed") from exc
        self._kokoro_tts.last_duration_ms = round((time.perf_counter() - started) * 1000)
        logger.info("tts_synthesized tts_ms=%d", self._kokoro_tts.last_duration_ms)
        output_emitter.push(pcm)
        output_emitter.flush()
