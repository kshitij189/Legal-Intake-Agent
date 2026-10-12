"""Audio-track building blocks (blueprint §13 Mode B): scenario schema, caller-speech synthesis
variations (voice, speed, noise, pauses), word error rate and aggregate audio metrics.

Pure functions only; the LiveKit/WebRTC orchestration lives in app/evaluation/audio_runner.py.
"""

import json
import re
import statistics
from pathlib import Path
from typing import Any, Self

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.metrics import ScenarioResult

RATE = 16_000


class AudioScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^AU\d{2}_[a-z0-9_]+$")
    description: str
    # "piper" (the ljspeech voice) or "kokoro:<voice>" - e.g. kokoro:bm_george.
    voice: str = Field(pattern=r"^(piper|kokoro:[a-z]{2}_[a-z]+)$")
    speed: float = Field(default=1.0, ge=0.6, le=1.6)  # Kokoro only
    snr_db: float | None = None  # white noise mixed in at this signal-to-noise ratio
    pause_s: float = Field(default=0.0, ge=0.0, le=3.0)  # silence where a turn has " | "
    turns: tuple[str, ...] = Field(min_length=1)
    keywords: tuple[str, ...] = ()  # must appear in the stored caller transcript (normalized)
    barge_in: bool = False  # talk over the agent's first reply with `interruption`
    interruption: str | None = None
    max_caller_wer: float = Field(default=0.2, ge=0.0, le=1.0)
    max_response_latency_s: float = Field(default=5.0, gt=0)

    @model_validator(mode="after")
    def _interruption(self) -> Self:
        if self.barge_in != (self.interruption is not None):
            raise ValueError(f"{self.id}: barge_in needs an interruption line (and vice versa)")
        if self.voice == "piper" and self.speed != 1.0:
            raise ValueError(f"{self.id}: speed variation needs a Kokoro voice")
        return self

    def spoken_text(self) -> list[str]:
        """What the caller actually says, turn by turn (pause markers removed)."""
        lines = [" ".join(part.strip() for part in turn.split("|")) for turn in self.turns]
        return [*lines, self.interruption] if self.interruption else lines


def load_audio_scenarios(path: Path) -> list[AudioScenario]:
    scenarios = [AudioScenario.model_validate(s) for s in json.loads(path.read_text("utf-8"))]
    if len({s.id for s in scenarios}) != len(scenarios):
        raise ValueError("audio scenario ids must be unique")
    return scenarios


# --- text normalization and WER -------------------------------------------------------------

_NUMBER_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}  # fmt: skip


def normalize_words(text: str) -> list[str]:
    """Lowercase, drop punctuation, spell single digits as digits and join digit runs, so
    "98765 43210", "98765, 43210" and "9876543210" all compare equal."""
    text = text.lower().replace("'", "")
    tokens = re.sub(r"[^a-z0-9+]+", " ", text).split()
    out: list[str] = []
    for token in tokens:
        token = _NUMBER_WORDS.get(token, token).lstrip("+")
        if token.isdigit() and out and out[-1].isdigit():
            out[-1] += token
        elif token:
            out.append(token)
    return out


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return round(edit_distance(ref, hyp) / len(ref), 3)


def missing_keywords(keywords: tuple[str, ...], transcript: str) -> list[str]:
    heard = " ".join(normalize_words(transcript))
    return [k for k in keywords if " ".join(normalize_words(k)) not in heard]


# --- signal transforms ----------------------------------------------------------------------


def add_noise(samples: np.ndarray, snr_db: float, seed: int = 7) -> np.ndarray:
    """White noise at the given SNR relative to the speech power (deterministic seed)."""
    speech = samples[np.abs(samples) > 1e-4]
    power = float(np.mean(speech**2)) if speech.size else 0.0
    noise = np.random.default_rng(seed).normal(0.0, 1.0, samples.shape).astype(np.float32)
    noise *= np.sqrt(power / (10 ** (snr_db / 10)))
    return np.clip(samples + noise, -1.0, 1.0).astype(np.float32)


def join_with_pauses(parts: list[np.ndarray], pause_s: float) -> np.ndarray:
    gap = np.zeros(int(pause_s * RATE), dtype=np.float32)
    out: list[np.ndarray] = []
    for i, part in enumerate(parts):
        if i:
            out.append(gap)
        out.append(part.astype(np.float32))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


# --- aggregate --------------------------------------------------------------------------------


def _pct(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(pct * (len(ordered) - 1)))], 2)


def aggregate_audio(results: list[ScenarioResult]) -> dict[str, Any]:
    ran = [r for r in results if r.details.get("caller_wer") is not None]
    latencies = [v for r in ran for v in r.details.get("response_latency_s", [])]
    barge = [r for r in ran if "barge_in_handled" in r.details]
    return {
        "scenarios": {s: sum(r.status == s for r in results) for s in ("PASS", "FAIL", "ERROR")},
        "caller_wer_mean": round(statistics.mean(r.details["caller_wer"] for r in ran), 3)
        if ran
        else None,
        "caller_wer_by_scenario": {r.scenario_id: r.details["caller_wer"] for r in ran},
        "agent_speech_wer_mean": round(
            statistics.mean(r.details["agent_wer"] for r in ran if "agent_wer" in r.details), 3
        )
        if any("agent_wer" in r.details for r in ran)
        else None,
        "keyword_recall": round(
            sum(r.details["keywords_heard"] for r in ran)
            / max(1, sum(r.details["keywords_total"] for r in ran)),
            3,
        ),
        "response_latency_s_p50": _pct(latencies, 0.5),
        "response_latency_s_p95": _pct(latencies, 0.95),
        "barge_in_handled": f"{sum(bool(r.details['barge_in_handled']) for r in barge)}"
        f"/{len(barge)}",
    }
