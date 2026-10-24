"""Transcribing a finished session again from its stored audio."""

import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import SegmentRow
from jarvis_live.ingest.retranscribe import RetranscribeFailed, retranscribe
from jarvis_live.stt.backend import STTUnavailable, Transcription
from tests.audio import TWO_UTTERANCES
from tests.conftest import FakeSTT
from tests.fakes import add_segments, make_session, new_device

SM = async_sessionmaker  # type: ignore[type-arg]


async def _texts(sm: SM, sid: uuid.UUID, source: str = "final") -> list[str]:
    async with sm() as db:
        return list(
            (
                await db.execute(
                    select(SegmentRow.text)
                    .where(SegmentRow.session_id == sid, SegmentRow.source == source)
                    .order_by(SegmentRow.start_ms)
                )
            )
            .scalars()
            .all()
        )


async def _session_with_audio(sm: SM, data_dir: Path) -> uuid.UUID:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id, status="finalizing")
    await add_segments(sm, sid, [(0, 1000, "me", "old words")])
    await add_segments(sm, sid, [(0, 1000, "me", "a draft")], source="draft")
    d = data_dir / "sessions" / str(sid)
    d.mkdir(parents=True)
    (d / "mic.pcm").write_bytes(TWO_UTTERANCES)  # no system recording: only mic is fed
    return sid


async def test_stored_audio_replaces_the_final_transcript(sm: SM, tmp_path: Path) -> None:
    sid = await _session_with_audio(sm, tmp_path)
    stt = FakeSTT()
    n = await retranscribe(
        sid,
        ["mic", "system"],
        settings=Settings(data_dir=tmp_path),
        sessionmaker=sm,
        bus=Bus(),
        stt=stt,
    )
    assert n == 2
    assert await _texts(sm, sid) == ["seg-1", "seg-2"]
    assert await _texts(sm, sid, "draft") == ["a draft"]  # the device's own drafts stay


class DownSTT(FakeSTT):
    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        if self.calls >= 1:
            raise STTUnavailable("all tiers down")
        return await super().transcribe(pcm, prompt)


async def test_a_lost_segment_fails_and_keeps_the_old_transcript(sm: SM, tmp_path: Path) -> None:
    sid = await _session_with_audio(sm, tmp_path)
    with pytest.raises(RetranscribeFailed):
        await retranscribe(
            sid,
            ["mic"],
            settings=Settings(data_dir=tmp_path),
            sessionmaker=sm,
            bus=Bus(),
            stt=DownSTT(),
        )
    assert await _texts(sm, sid) == ["old words"]


async def test_no_stored_audio_fails_and_keeps_the_old_transcript(sm: SM, tmp_path: Path) -> None:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id, status="finalizing")
    await add_segments(sm, sid, [(0, 1000, "me", "old words")])
    with pytest.raises(RetranscribeFailed):
        await retranscribe(
            sid,
            ["mic"],
            settings=Settings(data_dir=tmp_path),
            sessionmaker=sm,
            bus=Bus(),
            stt=FakeSTT(),
        )
    assert await _texts(sm, sid) == ["old words"]


async def test_it_reports_how_much_of_the_recording_is_transcribed(sm: SM, tmp_path: Path) -> None:
    sid = await _session_with_audio(sm, tmp_path)
    probes: list[Callable[[], float | None]] = []
    seen: list[float | None] = []

    class WatchingSTT(FakeSTT):
        async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
            seen.append(probes[0]())  # how far it is when each segment reaches STT
            return await super().transcribe(pcm, prompt)

    await retranscribe(
        sid,
        ["mic"],
        settings=Settings(data_dir=tmp_path),
        sessionmaker=sm,
        bus=Bus(),
        stt=WatchingSTT(),
        on_progress=probes.append,
    )
    assert len(probes) == 1
    assert len(seen) == 2 and all(p is not None and p < 1 for p in seen)
    assert seen[0] < seen[1]  # type: ignore[operator]
    assert probes[0]() == 1.0
