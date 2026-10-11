"""Small, dependency-light audio helpers shared by the STT adapter and the smoke tests."""

import numpy as np
from numpy.typing import NDArray

WHISPER_SAMPLE_RATE = 16_000


def pcm16_to_mono_float(data: bytes | memoryview, num_channels: int) -> NDArray[np.float32]:
    """Interleaved 16-bit PCM -> mono float32 in [-1, 1]."""
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if num_channels > 1:
        samples = samples.reshape(-1, num_channels).mean(axis=1)
    return samples


def resample_linear(
    samples: NDArray[np.float32], source_rate: int, target_rate: int
) -> NDArray[np.float32]:
    """Linear-interpolation resampling: adequate for speech recognition input."""
    if source_rate == target_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    target_len = round(samples.size * target_rate / source_rate)
    positions = np.linspace(0, samples.size - 1, target_len)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def to_whisper_input(
    data: bytes | memoryview, sample_rate: int, num_channels: int
) -> NDArray[np.float32]:
    return resample_linear(
        pcm16_to_mono_float(data, num_channels), sample_rate, WHISPER_SAMPLE_RATE
    )


def float_to_pcm16(samples: NDArray[np.float32]) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()
