"""Automated end-to-end voice smoke test over real WebRTC (no human, no LLM quota).

1. Starts scripts/voice_smoke_worker.py (real STT/TTS/VAD, deterministic scripted model).
2. A synthetic caller joins a fresh LiveKit room, waits for the spoken greeting, then speaks a
   Piper-synthesised sentence into the room.
3. Records the agent's audio, transcribes it with Whisper, measures response latency, hangs up.
4. Checks the database: VOICE session, transcript turns, saved deadline, escalation, and that the
   hang-up left the unfinished intake INCOMPLETE.

Usage: .venv\\Scripts\\python -u scripts/voice_e2e_smoke.py [--report FILE]
Requires `docker compose up -d postgres livekit`.
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from livekit import rtc
from sqlalchemy import select

from app.config import REPO_ROOT, get_settings
from app.db.session import make_engine, make_session_factory
from app.domain import intake_service
from app.main import voice_token
from app.models import CallSession, Escalation, IntakeRecord, TranscriptTurn
from app.voice.audio import pcm16_to_mono_float, resample_linear
from app.voice.stt import WhisperSTT
from app.voice.tts import PiperTTS

RATE = 16_000
FRAME = RATE // 100  # 10 ms
SPEECH_RMS = 0.02  # float amplitude regarded as speech
CALLER_LINE = "Hello. My name is Test Caller, and I must respond to a notice by tomorrow."
INTERRUPTION = "Sorry to interrupt. My phone number is 98765 43210."


@dataclass
class AgentAudio:
    frames: list[tuple[float, np.ndarray]] = field(default_factory=list)

    def speech_times(self) -> list[float]:
        return [t for t, f in self.frames if float(np.sqrt(np.mean(f**2))) > SPEECH_RMS]

    def audio_between(self, start: float, end: float) -> np.ndarray:
        parts = [f for t, f in self.frames if start <= t <= end]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


async def wait_for_utterance(
    audio: AgentAudio, after: float, timeout: float
) -> tuple[float, float]:
    """Wait until the agent starts speaking after `after`, then until 1.5 s of silence."""
    deadline = time.monotonic() + timeout
    start = None
    while time.monotonic() < deadline:
        speech = [t for t in audio.speech_times() if t > after]
        if speech:
            start = start or speech[0]
            if time.monotonic() - speech[-1] > 1.5:
                return start, speech[-1]
        await asyncio.sleep(0.1)
    raise TimeoutError(f"no complete agent utterance within {timeout:.0f}s (started={start})")


async def wait_for_speech_start(audio: AgentAudio, after: float, timeout: float) -> float:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        speech = [t for t in audio.speech_times() if t > after]
        if speech:
            return speech[0]
        await asyncio.sleep(0.05)
    raise TimeoutError("agent did not start speaking")


async def speak(source: rtc.AudioSource, samples: np.ndarray) -> float:
    """Play samples into the room in real time, plus 1 s of silence; returns speech end."""
    padded = np.concatenate([samples, np.zeros(RATE, dtype=np.float32)])
    pcm16 = (np.clip(padded, -1, 1) * 32767).astype(np.int16)
    for i in range(0, len(pcm16) - FRAME + 1, FRAME):
        await source.capture_frame(rtc.AudioFrame(pcm16[i : i + FRAME].tobytes(), RATE, 1, FRAME))
    await source.wait_for_playout()
    return time.monotonic() - 1.0


def start_worker(log_path: Path) -> subprocess.Popen[bytes]:
    log = log_path.open("wb")
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "voice_smoke_worker.py"), "start"],
        cwd=REPO_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


async def wait_for_registration(log_path: Path, timeout: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        if "registered worker" in text:
            return time.monotonic() - started
        await asyncio.sleep(0.5)
    raise TimeoutError("worker did not register with LiveKit")


async def run(report_path: Path | None, barge_in: bool) -> int:
    settings = get_settings()
    log_path = REPO_ROOT / "voice_smoke_worker.log"
    worker = start_worker(log_path)
    result: dict[str, Any] = {"mode": "barge-in" if barge_in else "basic", "checks": {}}
    try:
        result["worker_registration_s"] = round(await wait_for_registration(log_path, 240), 1)

        tts = PiperTTS(voice_path=settings.tts_voice_path)
        pcm = tts.synthesize_pcm(CALLER_LINE)
        caller = resample_linear(pcm16_to_mono_float(pcm, 1), tts.sample_rate, RATE)
        interruption = resample_linear(
            pcm16_to_mono_float(tts.synthesize_pcm(INTERRUPTION), 1), tts.sample_rate, RATE
        )
        stt = WhisperSTT(
            model_size=settings.stt_model, download_root=settings.models_dir / "whisper"
        )
        stt.load()

        token = voice_token()
        room = rtc.Room()
        audio = AgentAudio()
        readers: list[asyncio.Task[None]] = []

        async def read(track: rtc.Track) -> None:
            async for event in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
                audio.frames.append((time.monotonic(), pcm16_to_mono_float(event.frame.data, 1)))

        @room.on("track_subscribed")
        def _subscribed(track: rtc.Track, *_: Any) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                readers.append(asyncio.ensure_future(read(track)))

        joined = time.monotonic()
        await room.connect(token.url, token.token)
        source = rtc.AudioSource(RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("caller-mic", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )

        greet_start, greet_end = await wait_for_utterance(audio, joined, timeout=120)
        result["greeting_after_join_s"] = round(greet_start - joined, 2)

        spoken_end = await speak(source, caller)
        if barge_in:
            # Talk over the agent's reply 1.5 s after it starts.
            reply_start = await wait_for_speech_start(audio, greet_end + 0.5, timeout=90)
            await asyncio.sleep(1.5)
            interrupted_end = await speak(source, interruption)
            reply_end = interrupted_end
            after_start, _ = await wait_for_utterance(audio, interrupted_end + 0.2, timeout=90)
            result["latency_after_interruption_s"] = round(after_start - interrupted_end, 2)
        else:
            reply_start, reply_end = await wait_for_utterance(audio, greet_end + 0.5, timeout=90)
        result["response_latency_s"] = round(reply_start - spoken_end, 2)

        greeting_text, _ = stt.transcribe_samples(
            audio.audio_between(greet_start - 0.3, greet_end + 0.3), "en"
        )
        reply_text, _ = stt.transcribe_samples(
            audio.audio_between(reply_start - 0.3, reply_end + 0.3), "en"
        )
        result["heard_greeting"] = greeting_text
        result["heard_reply"] = reply_text

        await source.aclose()
        await room.disconnect()  # hang up mid-intake
        for reader in readers:
            reader.cancel()
        await asyncio.sleep(6)  # let the worker close the session

        sessions = make_session_factory(make_engine(settings.database_url))
        with sessions() as s:
            call = s.scalars(select(CallSession).order_by(CallSession.started_at.desc())).first()
            assert call is not None
            intake = s.get(IntakeRecord, call.intake_id) if call.intake_id else None
            turns = s.scalars(
                select(TranscriptTurn)
                .where(TranscriptTurn.call_session_id == call.id)
                .order_by(TranscriptTurn.seq)
            ).all()
            rules = (
                s.scalars(
                    select(Escalation.rule_id).where(Escalation.intake_id == call.intake_id)
                ).all()
                if call.intake_id
                else []
            )
            fields = intake_service.intake_fields(intake) if intake else {}
            interrupted_flags = [(str(t.speaker), t.interrupted) for t in turns]
            result["db"] = {
                "channel": str(call.channel),
                "session_status": str(call.status),
                "intake_status": str(intake.status) if intake else None,
                "reported_deadline": fields["reported_deadline"].value if intake else None,
                "escalations": list(rules),
                "transcript": [(str(t.speaker), t.text, t.processing_ms) for t in turns],
                "interrupted_flags": interrupted_flags,
            }
            caller_turns = [t.text for t in turns if str(t.speaker) == "CALLER"]

        checks = result["checks"]
        # Proper nouns vary in recognition ("Northster"); check the role and boundary wording.
        checks["greeting_heard"] = all(
            w in greeting_text.lower() for w in ("intake assistant", "legal advice", "transcribed")
        )
        checks["reply_heard"] = "deadline" in reply_text.lower()
        if barge_in:
            checks["reply_marked_interrupted"] = ("AGENT", True) in interrupted_flags
            checks["greeting_not_interrupted"] = interrupted_flags[0] == ("AGENT", False)
            checks["interruption_transcribed"] = any("number" in t.lower() for t in caller_turns)
            checks["agent_answered_interruption"] = (
                sum(1 for sp, _ in interrupted_flags if sp == "AGENT") >= 3
            )
        checks["caller_transcribed"] = any(
            "notice" in t.lower() and "tomorrow" in t.lower() for t in caller_turns
        )
        checks["voice_session"] = result["db"]["channel"] == "VOICE"
        checks["deadline_saved_and_escalated"] = (
            result["db"]["reported_deadline"] is not None and "imminent_deadline" in rules
        )
        checks["hangup_kept_partial_state"] = (
            result["db"]["session_status"] == "DISCONNECTED"
            and result["db"]["intake_status"] == "INCOMPLETE"
        )
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=20)
        except subprocess.TimeoutExpired:
            worker.kill()

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    result["stage_timings_ms"] = {
        name: [int(v) for v in re.findall(rf"{name}=(\d+)", log_text)]
        for name in ("stt_ms", "tts_ms", "processing_ms")
    }
    passed = all(result["checks"].values()) and bool(result["checks"])
    print(json.dumps(result, indent=2, default=str))
    print("PASS" if passed else "FAIL")
    if report_path:
        report_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--barge-in", action="store_true", help="interrupt the agent's reply")
    args = parser.parse_args()
    return asyncio.run(run(args.report, args.barge_in))


if __name__ == "__main__":
    sys.exit(main())
