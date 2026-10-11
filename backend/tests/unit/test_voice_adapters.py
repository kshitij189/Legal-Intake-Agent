import asyncio
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions

from app.voice.audio import float_to_pcm16, pcm16_to_mono_float, resample_linear, to_whisper_input
from app.voice.stt import WhisperSTT
from app.voice.tts import PiperTTS

NO_RETRY = APIConnectOptions(max_retry=0, timeout=5)


def tone(seconds: float, rate: int) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return float_to_pcm16((0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))


def test_stereo_is_averaged_and_scaled() -> None:
    stereo = np.array([1000, 3000, -2000, -4000], dtype=np.int16).tobytes()
    mono = pcm16_to_mono_float(stereo, 2)
    assert mono.tolist() == pytest.approx([2000 / 32768, -3000 / 32768])


def test_resampling_to_whisper_rate() -> None:
    samples = to_whisper_input(tone(1.0, 48_000), 48_000, 1)
    assert samples.dtype == np.float32 and samples.size == 16_000
    assert resample_linear(samples, 16_000, 16_000) is not None


class FakeWhisper:
    def __init__(self, segments: list[Any] | Exception) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, samples: Any, **kwargs: Any) -> tuple[list[Any], Any]:
        self.calls.append({"size": samples.size, **kwargs})
        if isinstance(self.segments, Exception):
            raise self.segments
        return self.segments, SimpleNamespace(language="en")


def segment(text: str, no_speech: float = 0.01, logprob: float = -0.1) -> SimpleNamespace:
    return SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob)


def frame(seconds: float = 0.5, rate: int = 48_000) -> rtc.AudioFrame:
    data = tone(seconds, rate)
    return rtc.AudioFrame(data, rate, 1, len(data) // 2)


def test_whisper_transcribes_and_drops_probable_silence() -> None:
    fake = FakeWhisper(
        [segment(" My number is 98,765 43,210."), segment(" Thank you.", no_speech=0.9)]
    )
    stt = WhisperSTT(model_size="base.en", model=fake)
    event = asyncio.run(stt.recognize(buffer=frame(), conn_options=NO_RETRY))
    [alternative] = event.alternatives
    assert alternative.text == "My number is 98,765 43,210."  # hallucinated "Thank you." dropped
    assert 0 < alternative.confidence <= 1
    assert fake.calls[0]["size"] == 8_000  # 0.5 s resampled to 16 kHz
    assert fake.calls[0]["language"] == "en" and fake.calls[0]["vad_filter"] is False
    assert stt.last_duration_ms is not None
    assert (stt.model, stt.provider) == ("base.en", "faster-whisper")


def test_silence_only_gives_an_empty_transcript() -> None:
    stt = WhisperSTT(model_size="base.en", model=FakeWhisper([segment(" you", no_speech=0.95)]))
    event = asyncio.run(stt.recognize(buffer=[frame(0.2), frame(0.2)], conn_options=NO_RETRY))
    assert event.alternatives[0].text == ""


def test_whisper_failure_is_reported_as_connection_error() -> None:
    stt = WhisperSTT(model_size="base.en", model=FakeWhisper(RuntimeError("model crashed")))
    with pytest.raises(APIConnectionError):
        asyncio.run(stt.recognize(buffer=frame(), conn_options=NO_RETRY))


class FakeVoice:
    config = SimpleNamespace(sample_rate=22_050)

    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str) -> list[SimpleNamespace]:
        self.texts.append(text)
        return [SimpleNamespace(audio_int16_bytes=tone(0.5, 22_050)) for _ in range(2)]


def test_piper_synthesis_streams_all_audio() -> None:
    voice = FakeVoice()
    tts = PiperTTS(voice=voice)
    assert (tts.sample_rate, tts.num_channels, tts.provider) == (22_050, 1, "piper")

    async def collect() -> int:
        samples = 0
        async with tts.synthesize("Hello there.", conn_options=NO_RETRY) as stream:
            async for audio in stream:
                samples += audio.frame.samples_per_channel
        return samples

    total = asyncio.run(collect())
    assert voice.texts == ["Hello there."]
    # Two 0.5 s chunks are all delivered; the emitter may pad the final frame slightly.
    assert 22_050 <= total < 22_050 + 22_050 // 20
    assert tts.last_duration_ms is not None


def test_missing_voice_file_is_a_clear_error(tmp_path: Any) -> None:
    with pytest.raises(FileNotFoundError, match="Piper voice not found"):
        PiperTTS(voice_path=tmp_path / "missing.onnx")
