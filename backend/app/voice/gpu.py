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


def resolve_tts_engine(engine: str) -> str:
    """`auto` -> Kokoro when its GPU path works (Kokoro on CPU is ~11x slower), else Piper."""
    if engine == "auto":
        return "kokoro" if onnx_cuda_available() else "piper"
    return engine
