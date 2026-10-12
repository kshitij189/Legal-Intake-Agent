"""Optional GPU support for the local voice models (see scripts/enable_gpu.py).

The CUDA runtime comes from NVIDIA pip wheels, not a system CUDA install:
- CUDA 12 (cuBLAS, runtime) for ctranslate2 / faster-whisper,
- CUDA 13 (cuBLAS, runtime, cuFFT, cuRAND) for onnxruntime-gpu 1.31 / Kokoro,
- one cuDNN 9 build (nvidia-cudnn-cu13), which serves both - measured working on 2026-10-12.
On Windows those DLL folders must be registered before the libraries load.
"""

import logging
import os
import site
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache
def register_cuda_dlls() -> list[str]:
    """Make the NVIDIA wheel DLLs loadable (Windows only; a no-op elsewhere)."""
    if sys.platform != "win32":
        return []
    added: list[str] = []
    for root in site.getsitepackages():
        nvidia = Path(root, "nvidia")
        for directory in [*nvidia.glob("*/bin"), *nvidia.glob("cu13/bin/x86_64")]:
            os.add_dll_directory(str(directory))
            os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"
            added.append(str(directory))
    return added


def whisper_cuda_available() -> bool:
    register_cuda_dlls()
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


def onnx_cuda_available() -> bool:
    register_cuda_dlls()
    try:
        import onnxruntime

        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def resolve_stt(device: str, compute_type: str) -> tuple[str, str]:
    """`auto` -> CUDA float16 when available, else CPU int8."""
    if device == "auto":
        device = "cuda" if whisper_cuda_available() else "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def gpu_total_memory_mib() -> int | None:
    """Total memory of the first NVIDIA GPU, or None if it cannot be read."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        return int(out.splitlines()[0].strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def resolve_tts_engine(engine: str, kokoro_min_gpu_mib: int = 0) -> str:
    """`auto` -> Kokoro when its GPU path works and the GPU is large enough, else Piper.

    Kokoro on CPU is ~11x slower. On the GPU, each voice process holds ~2 GB (Whisper + Kokoro)
    and LiveKit keeps the active process plus a spare, so a 4 GB card saturates by the 3rd-4th
    consecutive call and calls degrade (Phase 6 finding). Below `kokoro_min_gpu_mib`, use Piper.
    """
    if engine != "auto":
        return engine
    if not onnx_cuda_available():
        return "piper"
    if kokoro_min_gpu_mib <= 0:
        return "kokoro"
    total = gpu_total_memory_mib()
    if total is None or total < kokoro_min_gpu_mib:
        logger.warning(
            "tts_auto_selected_piper gpu_mib=%s kokoro_min_gpu_mib=%s", total, kokoro_min_gpu_mib
        )
        return "piper"
    return "kokoro"
