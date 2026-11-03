"""Which paths have a publisher, and how many audio tracks each carries (MediaMTX API v3)."""

import base64
import json
import secrets

import httpx
import pytest

from jarvis_live.streams.auth import ReaderCredential
from jarvis_live.streams.mediamtx import MediaMtx

# Made up per run: no credential-looking literal in the repo.
READER = ReaderCredential(user="jarvis-ingest", password=secrets.token_urlsafe(12))


def _path(name: str, online: bool, *codecs: str, legacy: bool = False) -> dict[str, object]:
    if legacy:  # before `online` and `tracks2`
        return {"name": name, "ready": online, "tracks": list(codecs)}
    return {"name": name, "online": online, "tracks2": [{"codec": c} for c in codecs]}


def _api(pages: list[list[dict[str, object]]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params.get("page", "0"))
        body = {"itemCount": sum(map(len, pages)), "pageCount": len(pages), "items": pages[page]}
        return httpx.Response(200, content=json.dumps(body))

    return httpx.MockTransport(handler)


async def test_lists_online_paths_with_their_audio_track_count() -> None:
    seen: list[httpx.Request] = []
    pages = [
        [
            _path("obs-desk", True, "H264", "MPEG-4 Audio", "MPEG-4 Audio"),
            _path("phone", True, "H265", "Opus"),
            _path("idle", False),
        ],
        [_path("old", True, "MPEG-4 Audio", legacy=True), _path("mute", True, "H264")],
    ]
    async with httpx.AsyncClient(transport=_api(pages, seen)) as client:
        api = MediaMtx("http://mediamtx:9997", READER, client)
        assert await api.online_paths() == {"obs-desk": 2, "phone": 1, "old": 1, "mute": 0}
    assert [r.url.path for r in seen] == ["/v3/paths/list"] * 2
    auth = seen[0].headers["authorization"]
    assert auth == "Basic " + base64.b64encode(f"jarvis-ingest:{READER.password}".encode()).decode()


async def test_an_api_error_raises() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await MediaMtx("http://mediamtx:9997", READER, client).online_paths()


def test_the_read_url_carries_the_reader_credential() -> None:
    api = MediaMtx("http://mediamtx:9997", READER, httpx.AsyncClient(), rtsp="rtsp://mediamtx:8554")
    assert (
        api.read_url("obs-desk") == f"rtsp://jarvis-ingest:{READER.password}@mediamtx:8554/obs-desk"
    )
