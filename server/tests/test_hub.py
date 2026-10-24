"""The ingest hub's handling of producer sockets that are already gone."""

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.websockets import WebSocketDisconnect

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.ingest.hub import IngestHub, close_producer
from jarvis_live.stt.backend import Transcription
from tests.audio import TWO_UTTERANCES
from tests.conftest import FakeSTT
from tests.fakes import make_session, new_device
from tests.helpers import eventually

SM = async_sessionmaker  # type: ignore[type-arg]


class GoneSocket:
    """A producer whose TCP connection died before the server noticed: closing it fails the
    way Starlette fails, with WebSocketDisconnect."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_calls += 1
        raise WebSocketDisconnect(code=1006)


async def test_closing_a_producer_that_is_already_gone_is_quiet() -> None:
    # A reconnecting producer supersedes the old connection; if that one is already dead,
    # the new connection must not fail because of it.
    gone = GoneSocket()
    await close_producer(gone, 4409)  # type: ignore[arg-type]
    assert gone.close_calls == 1


async def test_ending_a_session_whose_producer_is_gone_still_finalizes(
    sm: SM, tmp_path: Any
) -> None:
    device_id, _ = await new_device(sm)
    sid = await make_session(sm, device_id)
    ended: list[uuid.UUID] = []
    hub = IngestHub(Settings(data_dir=tmp_path), sm, Bus(), FakeSTT(), on_end=ended.append)
    state = await hub.get(sid, ["mic", "system"])
    state.producer = GoneSocket()  # type: ignore[assignment]

    assert await hub.end(sid) is True
    assert ended == [sid]  # the finalizer is started
    async with sm() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None and row.status == "finalizing"


class BlockedSTT(FakeSTT):
    """Transcribes nothing until ``release`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        await self.release.wait()
        return await super().transcribe(pcm, prompt)


async def test_an_ending_session_reports_how_far_its_last_segments_are(
    sm: SM, tmp_path: Any
) -> None:
    device_id, _ = await new_device(sm)
    sid = await make_session(sm, device_id)
    stt = BlockedSTT()
    hub = IngestHub(Settings(data_dir=tmp_path), sm, Bus(), stt)
    state = await hub.get(sid, ["mic"])
    state.pipeline.feed("mic", TWO_UTTERANCES)
    assert hub.draining_progress(sid) is None  # still live: a stream has no end

    ending = asyncio.create_task(hub.end(sid))
    await eventually(lambda: hub.draining_progress(sid) is not None)
    assert hub.draining_progress(sid) < 1  # type: ignore[operator]
    stt.release.set()
    await ending
    assert hub.draining_progress(sid) is None
