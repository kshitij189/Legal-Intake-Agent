"""Browser smoke test of the voice page: real Chromium, real page, fake microphone (a WAV file).

Starts the web app (uvicorn) and the smoke worker (scripted model, real STT/TTS), opens
http://127.0.0.1:8000/voice, clicks "Start call", lets the caller line play through the page's
microphone, then clicks "End call" and checks the database.

Usage: .venv\\Scripts\\python -u scripts/voice_browser_smoke.py [--report FILE] [--headed]
"""

import argparse
import json
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
from playwright.sync_api import sync_playwright
from sqlalchemy import select

from app.config import REPO_ROOT, get_settings
from app.db.session import make_engine, make_session_factory
from app.models import CallSession, IntakeRecord, TranscriptTurn
from app.voice.audio import pcm16_to_mono_float, resample_linear
from app.voice.tts import PiperTTS

CALLER_LINE = "Hello. My name is Test Caller, and I must respond to a notice by tomorrow."
WAV_RATE = 48_000


def write_caller_wav(path: Path) -> None:
    """Silence while the greeting plays, the caller line, then silence (Chrome loops the file)."""
    settings = get_settings()
    tts = PiperTTS(voice_path=settings.tts_voice_path)
    line = resample_linear(
        pcm16_to_mono_float(tts.synthesize_pcm(CALLER_LINE), 1), tts.sample_rate, WAV_RATE
    )
    samples = np.concatenate([np.zeros(WAV_RATE * 22), line, np.zeros(WAV_RATE * 60)]).astype(
        np.float32
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(WAV_RATE)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def wait_for_log(path: Path, needle: str, timeout: float) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if path.exists() and needle in path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.5)
    raise TimeoutError(f"{needle!r} not seen in {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    settings = get_settings()

    wav_path = REPO_ROOT / "voice_browser_caller.wav"
    write_caller_wav(wav_path)
    worker_log = REPO_ROOT / "voice_smoke_worker.log"
    web_log = REPO_ROOT / "voice_web.log"
    worker = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "scripts" / "voice_smoke_worker.py"), "start"],
        cwd=REPO_ROOT,
        stdout=worker_log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    web = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(settings.voice_web_port),
        ],
        cwd=REPO_ROOT,
        stdout=web_log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    result: dict[str, Any] = {"checks": {}}
    try:
        wait_for_log(worker_log, "registered worker", 240)
        wait_for_log(web_log, "Uvicorn running", 60)
        started_at = time.time()
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not args.headed,
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    f"--use-file-for-fake-audio-capture={wav_path}",
                    "--autoplay-policy=no-user-gesture-required",
                ],
            )
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{settings.voice_web_port}/voice")
            page.click("#start")
            page.wait_for_selector("#log >> text=Assistant audio connected", timeout=60_000)
            result["page_status_after_connect"] = page.inner_text("#status")
            time.sleep(40)  # greeting, caller line at ~22 s, agent reply
            page.click("#end")
            page.wait_for_selector("#status >> text=Call ended", timeout=15_000)
            result["page_log"] = page.inner_text("#log").splitlines()
            browser.close()
        time.sleep(6)

        sessions = make_session_factory(make_engine(settings.database_url))
        with sessions() as s:
            call = s.scalars(select(CallSession).order_by(CallSession.started_at.desc())).first()
            assert call is not None
            turns = s.scalars(
                select(TranscriptTurn)
                .where(TranscriptTurn.call_session_id == call.id)
                .order_by(TranscriptTurn.seq)
            ).all()
            intake = s.get(IntakeRecord, call.intake_id) if call.intake_id else None
            result["db"] = {
                "session_started_after_test_start": call.started_at.timestamp() >= started_at - 5,
                "channel": str(call.channel),
                "session_status": str(call.status),
                "intake_status": str(intake.status) if intake else None,
                "transcript": [(str(t.speaker), t.text) for t in turns],
            }
        caller = [t for sp, t in result["db"]["transcript"] if sp == "CALLER"]
        agent = [t for sp, t in result["db"]["transcript"] if sp == "AGENT"]
        checks = result["checks"]
        checks["page_received_agent_audio"] = True  # wait_for_selector above would have failed
        checks["fresh_voice_session"] = (
            result["db"]["session_started_after_test_start"] and result["db"]["channel"] == "VOICE"
        )
        checks["browser_mic_transcribed"] = any(
            "notice" in t.lower() and "tomorrow" in t.lower() for t in caller
        )
        checks["agent_replied"] = len(agent) >= 2
        checks["end_call_kept_partial_state"] = (
            result["db"]["session_status"] == "DISCONNECTED"
            and result["db"]["intake_status"] == "INCOMPLETE"
        )
    finally:
        for proc in (web, worker):
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        wav_path.unlink(missing_ok=True)

    passed = bool(result["checks"]) and all(result["checks"].values())
    print(json.dumps(result, indent=2, default=str))
    print("PASS" if passed else "FAIL")
    if args.report:
        args.report.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
