"""Provisional speaker labels while a session streams: best effort, never in STT's way."""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import SegmentRow, SessionSpeaker, SpeakerProfile
from jarvis_live.diarize.client import DiarizeUnavailable, Embedding
from jarvis_live.ingest.pipeline import SessionPipeline
from jarvis_live.protocol import Segment, SpeakerUpdate
from jarvis_live.users import create_user
from tests.audio import TWO_UTTERANCES
from tests.conftest import FakeSTT
from tests.fakes import make_session, new_device

SM = async_sessionmaker  # type: ignore[type-arg]
MODEL = "wespeaker-test"
ANDREW = [1.0, 0.0, 0.0]
DANA = [0.0, 1.0, 0.0]


class FakeEmbedder:
    """Answers with the next scripted print (or raises it); can be held back with ``gate``."""

    def __init__(self, *prints: list[float] | Exception) -> None:
        self.prints = list(prints)
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def embed(self, pcm: bytes) -> Embedding:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        out = self.prints.pop(0)
        if isinstance(out, Exception):
            raise out
        return Embedding(out, MODEL)


class Rig:
    def __init__(self, sm: SM, tmp_path: Path) -> None:
        self.sm, self.bus = sm, Bus()
        self.settings = Settings(data_dir=tmp_path, speaker_live_min_ms=1000)

    async def start(
        self, embedder: Any, *, user_id: uuid.UUID | None = None
    ) -> tuple[SessionPipeline, uuid.UUID]:
        user = user_id or (await create_user(self.sm, "T", email=f"{uuid.uuid4()}@x.test")).id
        device, _ = await new_device(self.sm, f"d-{uuid.uuid4()}", user_id=user)
        sid = await make_session(self.sm, device)
        pipeline = SessionPipeline(
            sid,
            ["mic"],
            settings=self.settings,
            sessionmaker=self.sm,
            bus=self.bus,
            stt=FakeSTT(),
            embedder=embedder,
        )
        pipeline.start()
        return pipeline, sid

    async def segments(self, sid: uuid.UUID) -> list[SegmentRow]:
        async with self.sm() as db:
            q = select(SegmentRow).where(SegmentRow.session_id == sid).order_by(SegmentRow.start_ms)
            return list((await db.execute(q)).scalars())

    async def speakers(self, sid: uuid.UUID) -> list[SessionSpeaker]:
        async with self.sm() as db:
            q = select(SessionSpeaker).where(SessionSpeaker.session_id == sid)
            return list((await db.execute(q.order_by(SessionSpeaker.ordinal))).scalars())


@pytest.fixture
def rig(sm: SM, tmp_path: Path) -> Rig:
    return Rig(sm, tmp_path)


async def _run(pipeline: SessionPipeline) -> None:
    pipeline.feed("mic", TWO_UTTERANCES)
    await pipeline.finish()  # drains transcription and the provisional labels
    await pipeline.close()


async def test_segments_get_provisional_voices_as_they_are_transcribed(rig: Rig) -> None:
    pipeline, sid = await rig.start(FakeEmbedder(ANDREW, DANA))
    sub = rig.bus.subscribe(str(sid), features=frozenset({"speaker_update"}))
    await _run(pipeline)

    first, second = await rig.segments(sid)
    assert (first.speaker_id, first.speaker_rev) == ("live_mic_0", 1)
    assert (second.speaker_id, second.speaker_rev) == ("live_mic_1", 1)
    assert first.speaker == "me"  # the channel side is untouched
    speakers = await rig.speakers(sid)
    assert [(s.speaker_id, s.ordinal, s.label) for s in speakers] == [
        ("live_mic_0", 1, None),
        ("live_mic_1", 2, None),
    ]
    assert speakers[0].embedding == ANDREW and speakers[0].model_id == MODEL

    updates = []
    while (e := await asyncio.wait_for(sub.get(), 1)) is not None:
        if isinstance(e, SpeakerUpdate):
            updates.append(e)
        if len(updates) == 2:
            break
    (a1,), (a2,) = (u.assignments for u in updates)
    assert (a1.speaker_id, a1.rev, a1.final) == ("live_mic_0", 1, False)
    assert (a2.speaker_id, a2.rev, a2.final) == ("live_mic_1", 1, False)
    assert [s.id for s in updates[0].speakers] == ["live_mic_0"]


async def test_an_enrolled_voice_is_named_as_it_speaks(rig: Rig) -> None:
    user = (await create_user(rig.sm, "A", email=f"{uuid.uuid4()}@x.test")).id
    async with rig.sm() as db:
        profile = SpeakerProfile(
            user_id=user, label="Andrew", is_owner=True, model_id=MODEL, embedding=ANDREW
        )
        db.add(profile)
        await db.commit()
        profile_id = profile.id
    pipeline, sid = await rig.start(FakeEmbedder([0.99, 0.05, 0.0], DANA), user_id=user)
    await _run(pipeline)

    me, other = await rig.speakers(sid)
    assert (me.label, me.is_owner, me.profile_id) == ("Andrew", True, profile_id)
    assert (other.label, other.is_owner, other.profile_id) == (None, False, None)


async def test_a_failing_embedder_costs_nothing(rig: Rig) -> None:
    pipeline, sid = await rig.start(FakeEmbedder(DiarizeUnavailable("down"), RuntimeError("boom")))
    sub = rig.bus.subscribe(str(sid), features=frozenset({"speaker_update"}))
    await _run(pipeline)

    segs = await rig.segments(sid)
    assert [s.text for s in segs] == ["seg-1", "seg-2"]
    assert all(s.speaker_id is None and s.speaker_rev == 0 for s in segs)
    assert await rig.speakers(sid) == []
    seen = []
    while not sub._queue.empty():  # type: ignore[attr-defined]
        seen.append(type(sub._queue.get_nowait()).__name__)  # type: ignore[attr-defined]
    assert "SpeakerUpdate" not in seen and seen.count("Segment") == 2


async def test_a_slow_embedder_does_not_hold_back_the_transcript(rig: Rig) -> None:
    embedder = FakeEmbedder(ANDREW, DANA)
    embedder.gate = asyncio.Event()
    pipeline, sid = await rig.start(embedder)
    sub = rig.bus.subscribe(str(sid), features=frozenset({"speaker_update"}))
    pipeline.feed("mic", TWO_UTTERANCES)
    try:
        for _ in range(2):  # both segments are published while the embedder is still stuck
            e = await asyncio.wait_for(sub.get(), 5)
            assert isinstance(e, Segment)
        assert [s.speaker_id for s in await rig.segments(sid)] == [None, None]
        embedder.gate.set()
        await pipeline.finish()
        assert [s.speaker_id for s in await rig.segments(sid)] == ["live_mic_0", "live_mic_1"]
    finally:
        embedder.gate.set()
        await pipeline.close()


async def test_a_clip_too_short_for_a_reliable_print_is_not_embedded(rig: Rig) -> None:
    rig.settings = Settings(data_dir=rig.settings.data_dir, speaker_live_min_ms=1800)
    embedder = FakeEmbedder(ANDREW)
    pipeline, sid = await rig.start(embedder)
    await _run(pipeline)
    first, second = await rig.segments(sid)  # 1.5 s of speech, then 2.0 s
    assert first.speaker_id is None and second.speaker_id == "live_mic_0"
    assert embedder.calls == 1


async def test_a_label_never_overwrites_the_final_pass(rig: Rig) -> None:
    embedder = FakeEmbedder(ANDREW, DANA)
    embedder.gate = asyncio.Event()
    pipeline, sid = await rig.start(embedder)
    pipeline.feed("mic", TWO_UTTERANCES)
    while len(await rig.segments(sid)) < 2:  # both saved, both waiting on the embedder
        await asyncio.sleep(0.01)
    async with rig.sm() as db:  # the authoritative pass lands first
        q = select(SegmentRow).where(SegmentRow.session_id == sid)
        for s in (await db.execute(q)).scalars():
            s.speaker_id, s.speaker_rev = "spk_mic_7", 2
        await db.commit()
    embedder.gate.set()
    await pipeline.finish()
    await pipeline.close()
    assert {(s.speaker_id, s.speaker_rev) for s in await rig.segments(sid)} == {("spk_mic_7", 2)}
