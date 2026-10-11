"""Enable the GPU path for local STT (faster-whisper) and Kokoro TTS. Idempotent.

Why a script and not plain dependencies:
- `onnxruntime-gpu` and `onnxruntime` install the same Python module, and several voice packages
  depend on CPU `onnxruntime`, so any later `pip install` can silently bring it back.
  Re-run this script after installing or upgrading dependencies.
- `nvidia-cudnn-cu12` and `nvidia-cudnn-cu13` write the same DLL file; only the CUDA 13 build is
  installed, and it serves both ctranslate2 (CUDA 12) and onnxruntime-gpu (CUDA 13).
Versions below are the combination measured working on 2026-10-12 (RTX 3050 Ti, driver 592).

Usage: .venv\\Scripts\\python scripts/enable_gpu.py
"""

import subprocess
import sys

CUDA_WHEELS = [
    "nvidia-cublas-cu12==12.9.2.10",
    "nvidia-cuda-runtime-cu12==12.9.79",
    "nvidia-cublas==13.8.0.4",
    "nvidia-cuda-runtime==13.4.92",
    "nvidia-cufft==12.4.0.43",
    "nvidia-curand==10.4.4.72",
]
SHARED_MODULE_WHEELS = ["onnxruntime-gpu==1.31.0", "nvidia-cudnn-cu13==9.27.0.42"]


def pip(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)


def main() -> int:
    pip("uninstall", "-y", "onnxruntime", "nvidia-cudnn-cu12")
    pip("install", *CUDA_WHEELS)
    # --force-reinstall: restore files a CPU onnxruntime or cuDNN-cu12 install may have overwritten.
    pip("install", "--force-reinstall", "--no-deps", *SHARED_MODULE_WHEELS)

    from app.voice.gpu import onnx_cuda_available, whisper_cuda_available

    whisper_ok, onnx_ok = whisper_cuda_available(), onnx_cuda_available()
    print(f"faster-whisper CUDA: {'yes' if whisper_ok else 'NO'}")
    print(f"onnxruntime CUDA (Kokoro): {'yes' if onnx_ok else 'NO'}")
    return 0 if whisper_ok and onnx_ok else 1


if __name__ == "__main__":
    sys.exit(main())
