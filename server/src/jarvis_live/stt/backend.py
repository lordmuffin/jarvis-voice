import io
import wave
from dataclasses import dataclass
from typing import Protocol

from jarvis_live.stt.tiers import STTUnavailable, TierPool

__all__ = ["STTBackend", "STTUnavailable", "Transcription", "WhisperBackend", "pcm_to_wav"]


@dataclass(frozen=True)
class Transcription:
    text: str
    tier: str


class STTBackend(Protocol):
    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        """Transcribe 16 kHz mono PCM16LE. Raises ``STTUnavailable`` if no tier could."""
        ...

    def current_tier(self) -> str | None: ...


def pcm_to_wav(pcm: bytes, sample_rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class WhisperBackend:
    """OpenAI-compatible Whisper via the tiered endpoint pool."""

    def __init__(self, pool: TierPool) -> None:
        self.pool = pool

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        text, tier = await self.pool.transcribe(pcm_to_wav(pcm), prompt)
        return Transcription(text=text, tier=tier)

    def current_tier(self) -> str | None:
        return self.pool.current_tier()
