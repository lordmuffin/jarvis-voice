"""MediaMTX, as the stream ingest sees it: which paths have a publisher, and where to read them.

Reached inside the cluster only (its API and RTSP ports are not exposed), with the reader
credential that the auth hook (``streams.auth``) accepts for ``read`` and ``api``.
"""

from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from jarvis_live.streams.auth import ReaderCredential

# PathTrackCodec values of MediaMTX's OpenAPI spec that are audio.
AUDIO_CODECS = frozenset(
    {
        "Opus",
        "FLAC",
        "Vorbis",
        "MPEG-4 Audio",
        "MPEG-4 Audio LATM",
        "MPEG-1/2 Audio",
        "AC3",
        "Speex",
        "G726",
        "G722",
        "G711",
        "LPCM",
    }
)


class MediaMtx:
    def __init__(
        self,
        api: str,
        reader: ReaderCredential,
        client: httpx.AsyncClient,
        *,
        rtsp: str = "rtsp://127.0.0.1:8554",
    ) -> None:
        self._api = api.rstrip("/")
        self._reader = reader
        self._client = client
        self._rtsp = rtsp.rstrip("/")

    async def online_paths(self) -> dict[str, int]:
        """Every path with a publisher, and how many audio tracks it carries."""
        found: dict[str, int] = {}
        page, pages = 0, 1
        while page < pages:
            r = await self._client.get(
                f"{self._api}/v3/paths/list",
                params={"page": page, "itemsPerPage": 100},
                auth=(self._reader.user, self._reader.password),
                timeout=5,
            )
            r.raise_for_status()
            body = r.json()
            pages = int(body.get("pageCount") or 1)
            for item in body.get("items") or []:
                if item.get("online", item.get("ready")):
                    found[str(item["name"])] = _audio_tracks(item)
            page += 1
        return found

    def read_url(self, name: str) -> str:
        """RTSP URL of path ``name``, with the reader credential in it."""
        parts = urlsplit(f"{self._rtsp}/{name}")
        creds = f"{quote(self._reader.user, safe='')}:{quote(self._reader.password, safe='')}"
        return urlunsplit(parts._replace(netloc=f"{creds}@{parts.netloc}"))


def _audio_tracks(item: dict[str, Any]) -> int:
    if "tracks2" in item:
        codecs = [t.get("codec") for t in item.get("tracks2") or []]
    else:  # MediaMTX before tracks2
        codecs = list(item.get("tracks") or [])
    return sum(1 for c in codecs if c in AUDIO_CODECS)
