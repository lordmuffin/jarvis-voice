"""Client for the diarizer sidecar (pyannote on the Spark): a recording in, speaker turns out."""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from jarvis_live.api.audio import wav_header
from jarvis_live.diarize.align import Turn
from jarvis_live.protocol import BYTES_PER_MS

_CHUNK = 1 << 20


class DiarizeUnavailable(Exception):
    """The diarizer could not produce turns for a recording."""


@dataclass(frozen=True)
class Diarization:
    turns: list[Turn]
    # One voiceprint per speaker, keyed like the turns' speaker names; all from ``model_id``.
    embeddings: dict[str, list[float]] = field(default_factory=dict)
    model_id: str | None = None


@dataclass(frozen=True)
class Embedding:
    vector: list[float]
    model_id: str


class Embedder(Protocol):
    async def embed(self, pcm: bytes) -> Embedding:
        """The voiceprint of one short 16 kHz mono PCM16LE clip. Raises ``DiarizeUnavailable``."""
        ...


class Diarizer(Protocol):
    async def diarize(self, pcm: Path) -> Diarization:
        """Turns (and voiceprints) for a raw 16 kHz mono PCM16LE file. Raises
        ``DiarizeUnavailable``."""
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
    """``POST <url>/diarize`` with a WAV body; answers
    ``{"turns": [{start_ms, end_ms, speaker}], "embeddings"?: {speaker: [float]},
    "model_id"?: str}`` (speaker: any string, stable within the recording)."""

    def __init__(
        self,
        url: str,
        client: httpx.AsyncClient,
        *,
        timeout_s: float | None,
        embed_timeout_s: float = 10.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._client = client
        self._timeout_s = timeout_s
        self._embed_timeout_s = embed_timeout_s

    async def diarize(self, pcm: Path) -> Diarization:
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
            body = r.json()
            vectors = body.get("embeddings") or {}
            embeddings = {str(name): [float(x) for x in vector] for name, vector in vectors.items()}
            model_id = body.get("model_id")
            if model_id is not None:
                model_id = str(model_id)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, OSError) as e:
            raise DiarizeUnavailable(repr(e)) from e
        if any(t.end_ms < t.start_ms for t in turns):
            raise DiarizeUnavailable("turn ends before it starts")
        return Diarization(turns, embeddings, model_id)

    async def embed(self, pcm: bytes) -> Embedding:
        """``POST <url>/embed`` with a WAV body; answers
        ``{"embedding": [float], "model_id": str}``."""
        try:
            r = await self._client.post(
                self._url + "/embed",
                content=wav_header(len(pcm)) + pcm,
                headers={"Content-Type": "audio/wav"},
                timeout=self._embed_timeout_s,
            )
            r.raise_for_status()
            body = r.json()
            vector = [float(x) for x in body["embedding"]]
            model_id = body["model_id"]
            if not vector or not isinstance(model_id, str):
                raise ValueError("no voiceprint")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as e:
            raise DiarizeUnavailable(repr(e)) from e
        return Embedding(vector, model_id)


def embedder_of(sidecar: Diarizer | None) -> Embedder | None:
    """The same sidecar as an ``Embedder``, if it can embed (the live labels need that)."""
    return sidecar if isinstance(sidecar, HttpDiarizer) or hasattr(sidecar, "embed") else None  # type: ignore[return-value]


def audio_ms(pcm: Path) -> int:
    return pcm.stat().st_size // BYTES_PER_MS
