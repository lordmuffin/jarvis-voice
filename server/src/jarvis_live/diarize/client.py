"""Client for the diarizer sidecar (pyannote on the Spark): a recording in, speaker turns out."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Protocol

import httpx

from jarvis_live.api.audio import wav_header
from jarvis_live.diarize.align import Turn
from jarvis_live.protocol import BYTES_PER_MS

_CHUNK = 1 << 20


class DiarizeUnavailable(Exception):
    """The diarizer could not produce turns for a recording."""


class Diarizer(Protocol):
    async def diarize(self, pcm: Path) -> list[Turn]:
        """Turns for a raw 16 kHz mono PCM16LE file. Raises ``DiarizeUnavailable``."""
        ...


def _wav_chunks(pcm: Path) -> Iterator[bytes]:
    yield wav_header(pcm.stat().st_size)
    with pcm.open("rb") as f:
        while chunk := f.read(_CHUNK):
            yield chunk


async def _stream(pcm: Path) -> AsyncIterator[bytes]:
    for chunk in _wav_chunks(pcm):  # a recording is never held in memory whole
        yield chunk


class HttpDiarizer:
    """``POST <url>/diarize`` with a WAV body; answers ``{"turns": [{start_ms, end_ms, speaker}]}``
    (speaker: any string, stable within the recording)."""

    def __init__(self, url: str, client: httpx.AsyncClient, *, timeout_s: float | None) -> None:
        self._url = url.rstrip("/")
        self._client = client
        self._timeout_s = timeout_s

    async def diarize(self, pcm: Path) -> list[Turn]:
        try:
            r = await self._client.post(
                self._url + "/diarize",
                content=_stream(pcm),
                headers={
                    "Content-Type": "audio/wav",
                    "Content-Length": str(44 + pcm.stat().st_size),
                },
                timeout=self._timeout_s,
            )
            r.raise_for_status()
            raw = r.json()["turns"]
            turns = [Turn(int(t["start_ms"]), int(t["end_ms"]), str(t["speaker"])) for t in raw]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as e:
            raise DiarizeUnavailable(repr(e)) from e
        if any(t.end_ms < t.start_ms for t in turns):
            raise DiarizeUnavailable("turn ends before it starts")
        return turns


def audio_ms(pcm: Path) -> int:
    return pcm.stat().st_size // BYTES_PER_MS
