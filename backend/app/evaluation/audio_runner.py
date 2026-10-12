"""Audio track (blueprint §13 Mode B): synthesized callers talk to the real voice pipeline over
LiveKit WebRTC; Whisper output stored by the agent is compared with what was said.

Order matters on a 4 GB GPU: caller speech is synthesized first (Kokoro on CPU), then the voice
worker runs alone on the GPU, and only after it stops is the agent's audio transcribed.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select

from app.config import REPO_ROOT, Settings
from app.db.session import make_engine, make_session_factory
from app.evaluation.audio import (
    RATE,
    AudioScenario,
    add_noise,
    aggregate_audio,
    join_with_pauses,
    load_audio_scenarios,
    missing_keywords,
    word_error_rate,
)
from app.evaluation.metrics import ERROR, FAIL, PASS, ScenarioResult
from app.models import CallSession, TranscriptTurn
from app.models.enums import SessionChannel

FRAME = RATE // 100  # 10 ms
SPEECH_RMS = 0.02
SILENCE_END_S = 1.5


# --- caller speech --------------------------------------------------------------------------


class CallerVoices:
    """Synthesizes caller turns; Kokoro runs on CPU so the GPU stays free for the worker."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._piper: Any = None
        self._kokoro: Any = None

    def _to_16k(self, samples: np.ndarray, rate: int) -> np.ndarray:
        from app.voice.audio import resample_linear

        return resample_linear(samples.astype(np.float32), rate, RATE)

    def speak(self, scenario: AudioScenario, text: str) -> np.ndarray:
        parts = [p.strip() for p in text.split("|") if p.strip()]
        audio = join_with_pauses([self._one(scenario, p) for p in parts], scenario.pause_s)
        return add_noise(audio, scenario.snr_db) if scenario.snr_db is not None else audio

    def _one(self, scenario: AudioScenario, text: str) -> np.ndarray:
        from app.voice.audio import pcm16_to_mono_float

        if scenario.voice == "piper":
            if self._piper is None:
                from app.voice.tts import PiperTTS

                self._piper = PiperTTS(voice_path=self._settings.tts_voice_path)
            pcm = self._piper.synthesize_pcm(text)
            return self._to_16k(pcm16_to_mono_float(pcm, 1), self._piper.sample_rate)
        if self._kokoro is None:
            from app.voice.tts_kokoro import load_kokoro

            self._kokoro = load_kokoro(
                self._settings.kokoro_model_path, self._settings.kokoro_voices_path, use_gpu=False
            )
        samples, rate = self._kokoro.create(
            text, voice=scenario.voice.split(":", 1)[1], speed=scenario.speed, lang="en-us"
        )
        return self._to_16k(np.asarray(samples, dtype=np.float32), rate)


# --- one call over WebRTC -------------------------------------------------------------------


@dataclass
class AgentAudio:
    frames: list[tuple[float, np.ndarray]] = field(default_factory=list)

    def speech_times(self) -> list[float]:
        return [t for t, f in self.frames if float(np.sqrt(np.mean(f**2))) > SPEECH_RMS]

    def between(self, start: float, end: float) -> np.ndarray:
        parts = [f for t, f in self.frames if start <= t <= end]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


@dataclass
class CallRecording:
    joined_at: datetime
    utterances: list[tuple[float, float]] = field(default_factory=list)  # agent speech windows
    latencies: list[float] = field(default_factory=list)
    agent_clips: list[np.ndarray] = field(default_factory=list)


async def _wait_for_utterance(
    audio: AgentAudio, after: float, timeout: float
) -> tuple[float, float]:
    deadline = time.monotonic() + timeout
    start = None
    while time.monotonic() < deadline:
        speech = [t for t in audio.speech_times() if t > after]
        if speech:
            start = start or speech[0]
            if time.monotonic() - speech[-1] > SILENCE_END_S:
                return start, speech[-1]
        await asyncio.sleep(0.1)
    raise TimeoutError(f"no complete agent utterance within {timeout:.0f}s")


async def _wait_for_speech_start(audio: AgentAudio, after: float, timeout: float) -> float:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        speech = [t for t in audio.speech_times() if t > after]
        if speech:
            return speech[0]
        await asyncio.sleep(0.05)
    raise TimeoutError("agent did not start speaking")


async def _play(source: Any, samples: np.ndarray) -> float:
    """Play into the room in real time plus 1 s of silence; returns when the speech ended."""
    from livekit import rtc

    padded = np.concatenate([samples, np.zeros(RATE, dtype=np.float32)])
    pcm16 = (np.clip(padded, -1, 1) * 32767).astype(np.int16)
    for i in range(0, len(pcm16) - FRAME + 1, FRAME):
        await source.capture_frame(rtc.AudioFrame(pcm16[i : i + FRAME].tobytes(), RATE, 1, FRAME))
    await source.wait_for_playout()
    return time.monotonic() - 1.0


async def run_call(
    turns: list[np.ndarray],
    interruption: np.ndarray | None,
    before_speaking: Callable[[], None] | None = None,
) -> CallRecording:
    from livekit import rtc

    from app.main import voice_token

    token = voice_token()
    room = rtc.Room()
    audio = AgentAudio()
    readers: list[asyncio.Task[None]] = []
    from app.voice.audio import pcm16_to_mono_float

    async def read(track: rtc.Track) -> None:
        async for event in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
            audio.frames.append((time.monotonic(), pcm16_to_mono_float(event.frame.data, 1)))

    @room.on("track_subscribed")
    def _subscribed(track: rtc.Track, *_: Any) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            readers.append(asyncio.ensure_future(read(track)))

    rec = CallRecording(joined_at=datetime.now(UTC))
    joined = time.monotonic()
    await room.connect(token.url, token.token)
    source = rtc.AudioSource(RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("caller-mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    try:
        last_end = (await _wait_for_utterance(audio, joined, timeout=120))[1]
        rec.utterances.append((joined, last_end))
        if before_speaking is not None:
            await asyncio.to_thread(before_speaking)
        for i, samples in enumerate(turns):
            spoken_end = await _play(source, samples)
            if i == 0 and interruption is not None:
                reply_start = await _wait_for_speech_start(audio, spoken_end, timeout=60)
                rec.latencies.append(reply_start - spoken_end)
                await asyncio.sleep(1.5)  # talk over the reply
                spoken_end = await _play(source, interruption)
            start, end = await _wait_for_utterance(audio, spoken_end, timeout=60)
            rec.latencies.append(start - spoken_end)
            rec.utterances.append((start, end))
            last_end = end
    finally:
        rec.agent_clips = [audio.between(s - 0.3, e + 0.3) for s, e in rec.utterances]
        await source.aclose()
        await room.disconnect()
        for reader in readers:
            reader.cancel()
    return rec


# --- worker process -------------------------------------------------------------------------


def start_worker(log_path: Path, tts_engine: str | None = None) -> subprocess.Popen[bytes]:
    """`tts_engine` overrides TTS_ENGINE for the worker only (None = the configured engine)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **({"TTS_ENGINE": tts_engine} if tts_engine else {})}
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "voice_eval_worker.py"), "start"],
        cwd=REPO_ROOT,
        env=env,
        stdout=log_path.open("wb"),
        stderr=subprocess.STDOUT,
    )


def wait_for_registration(log_path: Path, timeout: float) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if log_path.exists() and "registered worker" in log_path.read_text("utf-8", "replace"):
            return
        time.sleep(0.5)
    raise TimeoutError("voice evaluation worker did not register with LiveKit")


READY_MARKER = "INFO:livekit.agents:job runner initialized"


def wait_for_ready_processes(log_path: Path, count: int, timeout: float = 180) -> None:
    """Every job makes LiveKit start a replacement process, which loads and warms Whisper and
    Kokoro on the same GPU. Measured 2026-10-12: when that warm-up overlaps a live call, the call
    loses caller speech and replies slow to 5-8 s. Speech-robustness scenarios therefore wait
    until `count` processes have finished initializing (--audio-back-to-back skips this)."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        text = log_path.read_text("utf-8", "replace") if log_path.exists() else ""
        if text.count(READY_MARKER) >= count:
            time.sleep(2)  # let the warm-up's last GPU work drain
            return
        time.sleep(0.5)
    raise TimeoutError(f"voice worker did not finish initializing process #{count}")


def stop_worker(worker: subprocess.Popen[bytes]) -> None:
    worker.terminate()
    try:
        worker.wait(timeout=20)
    except subprocess.TimeoutExpired:
        worker.kill()


# --- the track ------------------------------------------------------------------------------


def stored_turns(settings: Settings, joined_at: datetime) -> list[tuple[str, str, bool]]:
    sessions = make_session_factory(make_engine(settings.database_url))
    with sessions() as s:
        call = s.scalars(
            select(CallSession)
            .where(
                CallSession.channel == SessionChannel.VOICE,
                CallSession.started_at >= joined_at - timedelta(seconds=5),
            )
            .order_by(CallSession.started_at)
        ).first()
        if call is None:
            return []
        turns = s.scalars(
            select(TranscriptTurn)
            .where(TranscriptTurn.call_session_id == call.id)
            .order_by(TranscriptTurn.seq)
        ).all()
        return [(str(t.speaker), t.text, t.interrupted) for t in turns]


def score(
    scenario: AudioScenario, rec: CallRecording, turns: list[tuple[str, str, bool]]
) -> ScenarioResult:
    result = ScenarioResult(scenario.id, "audio", "audio", PASS)
    caller_text = " ".join(text for speaker, text, _ in turns if speaker == "CALLER")
    reference = " ".join(scenario.spoken_text())
    missing = missing_keywords(scenario.keywords, caller_text)
    d = result.details
    d.update(
        voice=scenario.voice,
        speed=scenario.speed,
        snr_db=scenario.snr_db,
        pause_s=scenario.pause_s,
        reference=reference,
        caller_transcript=caller_text,
        caller_wer=word_error_rate(reference, caller_text),
        keywords_total=len(scenario.keywords),
        keywords_heard=len(scenario.keywords) - len(missing),
        response_latency_s=[round(v, 2) for v in rec.latencies],
        stored_turns=[[sp, tx, it] for sp, tx, it in turns],
    )
    if not caller_text:
        result.problems.append("no caller speech was transcribed and stored")
    if d["caller_wer"] > scenario.max_caller_wer:
        result.problems.append(f"caller WER {d['caller_wer']} > {scenario.max_caller_wer}")
    if missing:
        result.problems.append(f"keywords not recognized: {missing}")
    slow = [v for v in rec.latencies if v > scenario.max_response_latency_s]
    if slow:
        result.problems.append(f"response latency above {scenario.max_response_latency_s}s: {slow}")
    if scenario.barge_in:
        agent_flags = [it for sp, _, it in turns if sp == "AGENT"]
        handled = (
            True in agent_flags
            and not agent_flags[0]
            and len(agent_flags) >= len(scenario.turns) + 2
            and not missing_keywords(scenario.keywords[-1:], caller_text)
        )
        d["barge_in_handled"] = handled
        if not handled:
            result.problems.append(f"barge-in not handled (agent interrupted flags {agent_flags})")
    result.status = FAIL if result.problems else PASS
    return result


def agent_speech_wer(
    settings: Settings, results: list[ScenarioResult], recordings: dict[str, CallRecording]
) -> None:
    """Round trip of the agent's own speech: TTS -> WebRTC -> Whisper vs the stored reply text.
    Each recorded utterance is matched with the closest stored agent turn; an interrupted turn
    is compared only with the part that could have been spoken (its first N words)."""
    from app.voice.stt import WhisperSTT

    stt = WhisperSTT(model_size=settings.stt_model, download_root=settings.models_dir / "whisper")
    stt.load()
    for result in results:
        rec = recordings.get(result.scenario_id)
        agent = [(tx, it) for sp, tx, it in result.details.get("stored_turns", []) if sp == "AGENT"]
        if rec is None or not agent:
            continue
        wers = []
        for clip in rec.agent_clips:
            heard, _ = stt.transcribe_samples(clip, settings.stt_language)
            spoken = len(heard.split())
            wers.append(
                min(
                    word_error_rate(" ".join(text.split()[:spoken]) if cut else text, heard)
                    for text, cut in agent
                )
            )
        result.details["agent_wer"] = round(float(np.mean(wers)), 3) if wers else None


def run_audio_track(
    settings: Settings, path: Path, args: argparse.Namespace
) -> tuple[list[ScenarioResult], dict[str, Any]]:
    scenarios = load_audio_scenarios(path)
    if args.only:
        scenarios = [s for s in scenarios if s.id in set(args.only.split(","))]
    if args.limit:
        scenarios = scenarios[: args.limit]
    if not scenarios:
        return [], {}

    voices = CallerVoices(settings)
    audio = {
        s.id: (
            [voices.speak(s, t) for t in s.turns],
            voices.speak(s, s.interruption) if s.interruption else None,
        )
        for s in scenarios
    }

    # One log per run: it is the evidence for whatever the run found.
    log_path = args.report_dir / f"voice_eval_worker-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.log"
    worker = start_worker(log_path, getattr(args, "audio_tts", None))
    results: list[ScenarioResult] = []
    recordings: dict[str, CallRecording] = {}
    try:
        wait_for_registration(log_path, 240)
        for n, scenario in enumerate(scenarios):
            started = time.monotonic()
            # One process was ready at registration; call n's replacement is process n + 2.
            gate = None
            if not getattr(args, "audio_back_to_back", False):

                def gate(n: int = n) -> None:
                    wait_for_ready_processes(log_path, n + 2)

            try:
                turns, interruption = audio[scenario.id]
                rec = asyncio.run(run_call(turns, interruption, gate))
                time.sleep(6)  # let the worker close the session and store the transcript
                recordings[scenario.id] = rec
                result = score(scenario, rec, stored_turns(settings, rec.joined_at))
            except Exception as exc:  # report, do not crash the run
                result = ScenarioResult(scenario.id, "audio", "audio", ERROR, note=repr(exc))
            result.duration_s = round(time.monotonic() - started, 1)
            results.append(result)
    finally:
        stop_worker(worker)
    agent_speech_wer(settings, results, recordings)
    return results, aggregate_audio(results)
