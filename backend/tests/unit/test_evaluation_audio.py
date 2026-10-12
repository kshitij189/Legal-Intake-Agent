"""Audio-track helpers: scenario schema, normalization, WER, signal transforms, aggregation."""

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from app.evaluation.audio import (
    RATE,
    AudioScenario,
    add_noise,
    aggregate_audio,
    join_with_pauses,
    load_audio_scenarios,
    missing_keywords,
    normalize_words,
    word_error_rate,
)
from app.evaluation.metrics import FAIL, PASS, ScenarioResult

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "scenarios" / "audio_scenarios.json"


def test_shipped_audio_scenarios_load() -> None:
    scenarios = load_audio_scenarios(FIXTURE)
    assert len(scenarios) >= 8
    assert any(s.barge_in for s in scenarios)
    assert any(s.snr_db is not None for s in scenarios)
    assert any(s.pause_s for s in scenarios)
    assert len({s.voice for s in scenarios}) >= 5


def scenario(**overrides: object) -> AudioScenario:
    base: dict[str, object] = {
        "id": "AU99_test",
        "description": "t",
        "voice": "kokoro:af_heart",
        "turns": ["hello"],
    }
    return AudioScenario.model_validate({**base, **overrides})


def test_schema_rules() -> None:
    with pytest.raises(ValidationError, match="interruption"):
        scenario(barge_in=True)
    with pytest.raises(ValidationError, match="Kokoro"):
        scenario(voice="piper", speed=1.2)
    with pytest.raises(ValidationError):
        scenario(voice="elevenlabs")
    s = scenario(turns=["a | b"], barge_in=True, interruption="c")
    assert s.spoken_text() == ["a b", "c"]


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("My number is 98765 43210.", ["my", "number", "is", "9876543210"]),
        ("98765, 43210", ["9876543210"]),
        ("+91 99887 76655", ["919988776655"]),
        ("nine eight seven", ["987"]),
        ("D'Souza, Priya!", ["dsouza", "priya"]),
    ],
)
def test_normalize_words(text: str, words: list[str]) -> None:
    assert normalize_words(text) == words


def test_word_error_rate() -> None:
    assert word_error_rate("call me on 98765 43210", "Call me on 9876543210.") == 0.0
    assert word_error_rate("a b c d", "a x c") == 0.5  # one substitution, one deletion
    assert word_error_rate("a b", "a b c d") == 1.0  # two insertions
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "noise") == 1.0


def test_missing_keywords() -> None:
    heard = "Hello my name is Priya Sharma, number 98765, 43210."
    assert missing_keywords(("Priya Sharma", "9876543210", "deadline"), heard) == ["deadline"]


def test_noise_and_pauses() -> None:
    tone = (0.3 * np.sin(np.linspace(0, 200 * np.pi, RATE))).astype(np.float32)
    noisy = add_noise(tone, snr_db=10)
    snr = 10 * np.log10(np.mean(tone**2) / np.mean((noisy - tone) ** 2))
    assert 9.0 < snr < 11.0
    assert np.array_equal(add_noise(tone, 10), noisy)  # deterministic
    joined = join_with_pauses([tone, tone], 0.5)
    assert len(joined) == 2 * RATE + RATE // 2


def test_aggregate_audio() -> None:
    a = ScenarioResult("AU01_a", "audio", "audio", PASS)
    a.details = {"caller_wer": 0.1, "agent_wer": 0.0, "keywords_heard": 3, "keywords_total": 4,
                 "response_latency_s": [1.0, 2.0]}  # fmt: skip
    b = ScenarioResult("AU02_b", "audio", "audio", FAIL)
    b.details = {"caller_wer": 0.3, "keywords_heard": 1, "keywords_total": 1,
                 "response_latency_s": [3.0], "barge_in_handled": False}  # fmt: skip
    m = aggregate_audio([a, b])
    assert m["caller_wer_mean"] == 0.2
    assert m["agent_speech_wer_mean"] == 0.0
    assert m["keyword_recall"] == 0.8
    assert m["response_latency_s_p50"] == 2.0
    assert m["barge_in_handled"] == "0/1"
    assert m["scenarios"] == {"PASS": 1, "FAIL": 1, "ERROR": 0}
