import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, SegmentRow, SessionSpeaker, SpeakerTurn
from jarvis_live.diarize.align import Turn
from jarvis_live.diarize.client import Diarization, DiarizeUnavailable
from jarvis_live.diarize.service import DiarizeService
from jarvis_live.protocol import SpeakerUpdate
from tests.fakes import add_segments, make_session, new_device

SM = async_sessionmaker  # type: ignore[type-arg]


class FakeDiarizer:
    """Answers per recording file name (``mic.pcm``); a value that is an exception is raised."""

    def __init__(self, by_channel: dict[str, list[Turn] | Diarization | Exception]) -> None:
        self.by_channel = by_channel
        self.calls: list[str] = []

    async def diarize(self, pcm: Path) -> Diarization:
        self.calls.append(pcm.stem)
        out = self.by_channel[pcm.stem]
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, Diarization) else Diarization(out)


class Rig:
    def __init__(self, sm: SM, tmp_path: Path) -> None:
        self.sm = sm
        self.bus = Bus()
        self.settings = Settings(data_dir=tmp_path)

    async def user(self) -> uuid.UUID:
        """A user of their own: enrolled voices are per user, and the database is shared."""
        from jarvis_live.users import create_user

        return (await create_user(self.sm, "Test", email=f"{uuid.uuid4()}@x.test")).id

    async def session(
        self, channels: tuple[str, ...] = ("mic", "system"), user_id: uuid.UUID = OWNER_USER_ID
    ) -> uuid.UUID:
        device, _ = await new_device(self.sm, f"d-{uuid.uuid4()}", user_id=user_id)
        sid = await make_session(self.sm, device, status="finalizing")
        for c in channels:
            d = self.settings.data_dir / "sessions" / str(sid)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{c}.pcm").write_bytes(bytes(64_000))
        return sid

    def service(self, diarizer: Any) -> DiarizeService:
        return DiarizeService(self.settings, self.sm, self.bus, diarizer)

    async def segments(self, sid: uuid.UUID) -> list[SegmentRow]:
        async with self.sm() as db:
            q = select(SegmentRow).where(SegmentRow.session_id == sid).order_by(SegmentRow.start_ms)
            return list((await db.execute(q)).scalars())


@pytest.fixture
def rig(sm: SM, tmp_path: Path) -> Rig:
    return Rig(sm, tmp_path)


SYSTEM_TURNS = [Turn(0, 4000, "SPEAKER_01"), Turn(4000, 9000, "SPEAKER_00")]


async def test_labels_segments_stores_turns_and_publishes_a_final_update(rig: Rig) -> None:
    sid = await rig.session()
    await add_segments(
        rig.sm,
        sid,
        [
            (0, 3000, "them", "Hello everyone"),
            (4500, 8500, "them", "I will send the quote"),
            (9000, 12000, "me", "Thanks"),
        ],
    )
    sub = rig.bus.subscribe(str(sid), features=frozenset({"speaker_update"}))
    diarizer = FakeDiarizer({"system": SYSTEM_TURNS, "mic": [Turn(8500, 12500, "SPEAKER_00")]})

    assert await rig.service(diarizer).run(sid, ["mic", "system"]) is True

    first, second, mine = await rig.segments(sid)
    # ids are per session, numbered by first appearance in time, not by the model's names
    assert (first.speaker_id, second.speaker_id) == ("spk_system_0", "spk_system_1")
    assert mine.speaker_id == "spk_mic_0"
    assert all(s.speaker_rev == 2 for s in (first, second, mine))
    assert first.speaker == "them" and mine.speaker == "me"  # the channel side is untouched

    async with rig.sm() as db:
        speakers = (
            (
                await db.execute(
                    select(SessionSpeaker)
                    .where(SessionSpeaker.session_id == sid)
                    .order_by(SessionSpeaker.ordinal)
                )
            )
            .scalars()
            .all()
        )
        turns = (
            (await db.execute(select(SpeakerTurn).where(SpeakerTurn.session_id == sid)))
            .scalars()
            .all()
        )
    assert [(s.speaker_id, s.ordinal, s.label) for s in speakers] == [
        ("spk_system_0", 1, None),
        ("spk_system_1", 2, None),
        ("spk_mic_0", 3, None),
    ]
    assert len(turns) == 3

    event = await sub.get()
    assert isinstance(event, SpeakerUpdate)
    assert {(a.segment_id, a.speaker_id, a.rev, a.final) for a in event.assignments} == {
        (str(first.id), "spk_system_0", 2, True),
        (str(second.id), "spk_system_1", 2, True),
        (str(mine.id), "spk_mic_0", 2, True),
    }
    assert [s.id for s in event.speakers] == ["spk_system_0", "spk_system_1", "spk_mic_0"]


async def test_a_segment_across_a_change_of_speaker_is_marked_mixed(rig: Rig) -> None:
    sid = await rig.session(("system",))
    await add_segments(rig.sm, sid, [(2000, 7000, "them", "long and mixed")])
    d = FakeDiarizer({"system": SYSTEM_TURNS})
    await rig.service(d).run(sid, ["system"])
    (seg,) = await rig.segments(sid)
    assert seg.mixed and seg.speaker_id == "spk_system_1"  # 3000 ms of SPEAKER_00 vs 2000 ms


@pytest.mark.parametrize(
    "failure", [DiarizeUnavailable("down"), RuntimeError("model crashed")], ids=["down", "crash"]
)
async def test_diarizer_failure_leaves_the_transcript_alone(rig: Rig, failure: Exception) -> None:
    sid = await rig.session()
    await add_segments(rig.sm, sid, [(0, 3000, "them", "Hello")])
    d = FakeDiarizer({"system": failure, "mic": failure})

    assert await rig.service(d).run(sid, ["mic", "system"]) is False

    (seg,) = await rig.segments(sid)
    assert seg.speaker_id is None and seg.speaker_rev == 0
    async with rig.sm() as db:
        assert (
            not (await db.execute(select(SpeakerTurn).where(SpeakerTurn.session_id == sid)))
            .scalars()
            .all()
        )


async def test_one_failing_channel_does_not_cost_the_other(rig: Rig) -> None:
    sid = await rig.session()
    await add_segments(rig.sm, sid, [(0, 3000, "them", "Hello"), (3500, 5000, "me", "Hi")])
    d = FakeDiarizer({"system": DiarizeUnavailable("x"), "mic": [Turn(3000, 5500, "A")]})
    assert await rig.service(d).run(sid, ["mic", "system"]) is True
    them, me = await rig.segments(sid)
    assert them.speaker_id is None and me.speaker_id == "spk_mic_0"


async def test_no_diarizer_or_no_recording_does_nothing(rig: Rig) -> None:
    sid = await rig.session(())
    await add_segments(rig.sm, sid, [(0, 3000, "them", "Hello")])
    assert await rig.service(None).run(sid, ["system"]) is False
    d = FakeDiarizer({})
    assert await rig.service(d).run(sid, ["system"]) is False  # no audio stored: not even asked
    assert d.calls == []


async def test_realign_uses_stored_turns_without_asking_the_diarizer(rig: Rig) -> None:
    sid = await rig.session(("system",))
    await add_segments(rig.sm, sid, [(0, 3000, "them", "old")])
    d = FakeDiarizer({"system": SYSTEM_TURNS})
    svc = rig.service(d)
    await svc.run(sid, ["system"])
    # a retranscription replaces the segments with different cuts
    async with rig.sm() as db:
        for s in (
            await db.execute(select(SegmentRow).where(SegmentRow.session_id == sid))
        ).scalars():
            await db.delete(s)
        await db.commit()
    await add_segments(rig.sm, sid, [(4500, 8000, "them", "new cut")])

    assert await svc.realign(sid) is True

    (seg,) = await rig.segments(sid)
    assert seg.speaker_id == "spk_system_1" and seg.speaker_rev == 2
    assert d.calls == ["system"]  # only the first run asked


async def test_running_twice_replaces_rather_than_duplicates(rig: Rig) -> None:
    sid = await rig.session(("system",))
    await add_segments(rig.sm, sid, [(0, 3000, "them", "Hello")])
    svc = rig.service(FakeDiarizer({"system": SYSTEM_TURNS}))
    await svc.run(sid, ["system"])
    await svc.run(sid, ["system"])
    async with rig.sm() as db:
        assert (
            len(
                (await db.execute(select(SpeakerTurn).where(SpeakerTurn.session_id == sid)))
                .scalars()
                .all()
            )
            == 2
        )
        assert (
            len(
                (await db.execute(select(SessionSpeaker).where(SessionSpeaker.session_id == sid)))
                .scalars()
                .all()
            )
            == 2
        )
    (seg,) = await rig.segments(sid)
    assert seg.speaker_rev == 3


async def test_a_diarizer_that_hangs_is_cut_off_at_the_timeout(sm: SM, tmp_path: Path) -> None:
    import asyncio

    class Hangs:
        async def diarize(self, pcm: Path) -> Diarization:
            await asyncio.sleep(30)
            return Diarization([])

    rig = Rig(sm, tmp_path)
    rig.settings = Settings(data_dir=tmp_path, diarize_timeout_min_s=0.05, diarize_timeout_ratio=0)
    sid = await rig.session(("system",))
    await add_segments(rig.sm, sid, [(0, 3000, "them", "Hello")])

    assert await asyncio.wait_for(rig.service(Hangs()).run(sid, ["system"]), 5) is False
    (seg,) = await rig.segments(sid)
    assert seg.speaker_id is None


def test_timeout_grows_with_the_recording(tmp_path: Path) -> None:
    cfg = Settings(data_dir=tmp_path, diarize_timeout_min_s=60, diarize_timeout_ratio=0.5)
    svc = DiarizeService(cfg, None, Bus(), None)  # type: ignore[arg-type]
    assert svc.timeout_s(10_000) == 60  # 10 s of audio: the floor
    assert svc.timeout_s(3_600_000) == 1800  # an hour: half of it


# --- enrolled voices -------------------------------------------------------------------------

MODEL = "wespeaker-test"
ANDREW_PRINT = [1.0, 0.0, 0.0]
DANA_PRINT = [0.0, 1.0, 0.0]


async def _enrol(
    sm: SM,
    label: str,
    vector: list[float],
    *,
    owner: bool = False,
    model: str = MODEL,
    user_id: uuid.UUID = OWNER_USER_ID,
) -> uuid.UUID:
    from jarvis_live.db.models import SpeakerProfile

    async with sm() as db:
        row = SpeakerProfile(
            user_id=user_id, label=label, is_owner=owner, model_id=model, embedding=vector
        )
        db.add(row)
        await db.commit()
        return row.id


def _voices(system: list[float], other: list[float]) -> Diarization:
    return Diarization(
        SYSTEM_TURNS,
        {"SPEAKER_01": system, "SPEAKER_00": other},  # SPEAKER_01 speaks first
        MODEL,
    )


async def _speakers_of(rig: Rig, sid: uuid.UUID) -> dict[str, SessionSpeaker]:
    async with rig.sm() as db:
        q = select(SessionSpeaker).where(SessionSpeaker.session_id == sid)
        return {s.speaker_id: s for s in (await db.execute(q)).scalars()}


async def test_an_enrolled_voice_is_recognised_by_name(rig: Rig) -> None:
    user = await rig.user()
    profile = await _enrol(rig.sm, "Andrew", ANDREW_PRINT, owner=True, user_id=user)
    sid = await rig.session(("system",), user)
    await add_segments(rig.sm, sid, [(0, 3000, "them", "mine"), (4500, 8500, "them", "theirs")])
    sub = rig.bus.subscribe(str(sid), features=frozenset({"speaker_update"}))
    voices = _voices([0.98, 0.1, 0.0], [0.0, 0.9, 0.1])

    await rig.service(FakeDiarizer({"system": voices})).run(sid, ["system"])

    speakers = await _speakers_of(rig, sid)
    me, other = speakers["spk_system_0"], speakers["spk_system_1"]
    assert (me.label, me.is_owner, me.profile_id) == ("Andrew", True, profile)
    assert (other.label, other.is_owner, other.profile_id) == (None, False, None)
    assert me.embedding == [0.98, 0.1, 0.0] and me.model_id == MODEL  # kept, to enrol from
    event = await sub.get()
    assert isinstance(event, SpeakerUpdate)
    by_id = {s.id: s for s in event.speakers}
    assert (by_id["spk_system_0"].label, by_id["spk_system_0"].enrolled) == ("Andrew", True)
    assert by_id["spk_system_0"].is_owner and not by_id["spk_system_1"].enrolled


async def test_two_enrolled_voices_each_find_their_own_profile(rig: Rig) -> None:
    user = await rig.user()
    await _enrol(rig.sm, "Andrew", ANDREW_PRINT, user_id=user)
    await _enrol(rig.sm, "Dana", DANA_PRINT, user_id=user)
    sid = await rig.session(("system",), user)
    await add_segments(rig.sm, sid, [(0, 3000, "them", "a")])
    await rig.service(FakeDiarizer({"system": _voices([0.1, 0.99, 0.0], [0.97, 0.1, 0.0])})).run(
        sid, ["system"]
    )
    speakers = await _speakers_of(rig, sid)
    assert (speakers["spk_system_0"].label, speakers["spk_system_1"].label) == ("Dana", "Andrew")


async def test_a_voice_print_from_another_model_is_not_compared(rig: Rig) -> None:
    user = await rig.user()
    await _enrol(rig.sm, "Andrew", ANDREW_PRINT, model="older-model", user_id=user)
    sid = await rig.session(("system",), user)
    await add_segments(rig.sm, sid, [(0, 3000, "them", "a")])
    await rig.service(FakeDiarizer({"system": _voices(ANDREW_PRINT, DANA_PRINT)})).run(
        sid, ["system"]
    )
    assert all(s.label is None for s in (await _speakers_of(rig, sid)).values())


async def test_someone_elses_enrolled_voices_are_not_used(rig: Rig) -> None:
    await _enrol(rig.sm, "Andrew", ANDREW_PRINT, user_id=await rig.user())
    sid = await rig.session(("system",), await rig.user())
    await add_segments(rig.sm, sid, [(0, 3000, "them", "a")])
    await rig.service(FakeDiarizer({"system": _voices(ANDREW_PRINT, DANA_PRINT)})).run(
        sid, ["system"]
    )
    assert all(s.label is None for s in (await _speakers_of(rig, sid)).values())


async def test_a_diarizer_without_voice_prints_just_leaves_voices_unnamed(rig: Rig) -> None:
    user = await rig.user()
    await _enrol(rig.sm, "Andrew", ANDREW_PRINT, user_id=user)
    sid = await rig.session(("system",), user)
    await add_segments(rig.sm, sid, [(0, 3000, "them", "a")])
    await rig.service(FakeDiarizer({"system": SYSTEM_TURNS})).run(sid, ["system"])
    speakers = await _speakers_of(rig, sid)
    assert len(speakers) == 2 and all(s.label is None for s in speakers.values())
