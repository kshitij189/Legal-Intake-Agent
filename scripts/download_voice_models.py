"""Download the local voice models into models/ (git-ignored).

- Piper voice en_US-ljspeech-medium (~61 MB; LJSpeech dataset: public domain)
- faster-whisper model from STT_MODEL (default base.en, ~145 MB)

Usage: .venv\\Scripts\\python scripts/download_voice_models.py
"""

import subprocess
import sys

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    piper_dir = settings.tts_voice_path.parent
    piper_dir.mkdir(parents=True, exist_ok=True)
    if not settings.tts_voice_path.exists():
        subprocess.run(
            [
                sys.executable,
                "-m",
                "piper.download_voices",
                settings.tts_voice_path.stem,
                "--download-dir",
                str(piper_dir),
            ],
            check=True,
        )
    print(f"piper voice: {settings.tts_voice_path}")

    from faster_whisper import WhisperModel

    WhisperModel(
        settings.stt_model,
        device=settings.stt_device,
        compute_type=settings.stt_compute_type,
        download_root=str(settings.models_dir / "whisper"),
    )
    print(f"whisper model: {settings.stt_model} in {settings.models_dir / 'whisper'}")


if __name__ == "__main__":
    main()
