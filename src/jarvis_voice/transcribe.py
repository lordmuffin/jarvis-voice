"""faster-whisper wrapper used by the capture API audio endpoint."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time

from faster_whisper import WhisperModel

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small.en")

_model: WhisperModel | None = None


class TranscribeError(RuntimeError):
    """Raised when the audio pipeline can't produce a transcript."""


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(audio_path: str) -> tuple[str, float, float]:
    """Convert audio at *audio_path* to 16 kHz mono WAV and transcribe it.

    Returns (transcript, audio_duration_seconds, transcribe_seconds), where
    transcribe_seconds is the wall-clock time spent in ffmpeg + Whisper.
    """
    started = time.monotonic()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", audio_path,
                    "-ar", "16000", "-ac", "1", "-f", "wav", wav_path,
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as err:
            raise TranscribeError("ffmpeg not installed on server") from err
        except subprocess.CalledProcessError as err:
            stderr = (err.stderr or b"").decode("utf-8", errors="replace").strip()
            tail = stderr.splitlines()[-1] if stderr else "unknown error"
            raise TranscribeError(f"ffmpeg failed to decode upload: {tail[:200]}") from err

        segments, info = _get_model().transcribe(wav_path, beam_size=5)
        transcript = " ".join(s.text.strip() for s in segments).strip()
        return transcript, float(info.duration), time.monotonic() - started
    finally:
        try:
            os.unlink(wav_path)
        except FileNotFoundError:
            pass
