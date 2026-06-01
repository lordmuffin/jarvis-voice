"""faster-whisper wrapper shared by the Telegram bot (Gate 2) and capture API (Gate 3)."""
from __future__ import annotations

import os
import subprocess
import tempfile

from faster_whisper import WhisperModel

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small.en")

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(audio_path: str) -> tuple[str, float]:
    """Convert audio at *audio_path* to 16 kHz mono WAV and transcribe it.

    Returns (transcript, duration_seconds).
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", audio_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", wav_path,
            ],
            check=True,
            capture_output=True,
        )
        segments, info = _get_model().transcribe(wav_path, beam_size=5)
        transcript = " ".join(s.text.strip() for s in segments).strip()
        return transcript, float(info.duration)
    finally:
        try:
            os.unlink(wav_path)
        except FileNotFoundError:
            pass
