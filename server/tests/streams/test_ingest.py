"""The stream ingest end to end, against the real app (uvicorn thread) and Postgres.

MediaMTX is replaced by ``FakePaths`` (which paths are online) and each RTSP read by a script that
runs the real decode command (``ffmpeg_command``) on the next prepared OBS-like clip, so a
publisher "going away" and "coming back" is a clip ending and the next one starting. No OBS and
no MediaMTX are needed.
"""

import asyncio
import shlex
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.client.transport import ReconnectPolicy
from jarvis_live.db.models import OWNER_USER_ID, AudioChunk, SegmentRow
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.streams import keys
from jarvis_live.streams.decode import ffmpeg_command
from jarvis_live.streams.ingest import StreamIngest
from tests.audio import TWO_UTTERANCES
from tests.client.conftest import script
from tests.conftest import ServerHandle
from tests.streams.clips import obs_clip, system_audio

SM = async_sessionmaker  # type: ignore[type-arg]
RUN = uuid.uuid4().hex[:6]  # the test database outlives one test: names must not repeat
N = {
    n: f"{n}-{RUN}"
    for n in [
        "obs-desk",
        "stopper",
        "dropper",
        "again",
        "phone",
        "silent",
        "no-such-key",
        "steady",
        "one",
        "two",
    ]
}
T = {
    t: f"{t} {RUN}"
    for t in [
        "obs profile",
        "stop test",
        "drop test",
        "again test",
        "phone test",
        "silent test",
        "steady test",
        "room test",
    ]
}
SECONDS = len(TWO_UTTERANCES) / 32_000


class FakePaths:
    """MediaMTX's view: path name -> audio track count, for the paths with a publisher."""

    def __init__(self) -> None:
        self.online: dict[str, int] = {}
        self.fail = False

    async def online_paths(self) -> dict[str, int]:
        if self.fail:
            raise httpx.ConnectError("mediamtx is down")
        return dict(self.online)

    def read_url(self, name: str) -> str:
        return name


class Clips:
    """Per path, the clips its publisher sends, one per (re)connect; then nothing."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def add(self, name: str, n: int, *, tracks: int = 2) -> None:
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        system = system_audio(SECONDS) if tracks == 2 else None
        obs_clip(d / f"clip{n}.ts", TWO_UTTERANCES, system)

    def argv(self, name: str, track: int) -> list[str]:
        d = self.root / name
        decode = shlex.join(ffmpeg_command("CLIP", track=track)).replace("CLIP", '"$clip"')
        body = (
            f'n=$(cat "{d}/count.{track}" 2>/dev/null || echo 0)\n'
            f'echo $((n + 1)) > "{d}/count.{track}"\n'
            f'clip="{d}/clip$n.ts"\n'
            '[ -f "$clip" ] || exec sleep 600\n'
            f"exec {decode}\n"
        )
        return [str(script(d / f"read.{track}.sh", body))]


@pytest.fixture
def paths() -> FakePaths:
    return FakePaths()


@pytest.fixture
def clips(tmp_path: Path) -> Clips:
    return Clips(tmp_path / "clips")


@pytest.fixture
def make_ingest(
    server: ServerHandle, token: str, sm: SM, paths: FakePaths, clips: Clips, tmp_path: Path
) -> Callable[..., StreamIngest]:
    async def ticket_for(session_id: uuid.UUID, user_id: uuid.UUID) -> str:
        assert user_id == OWNER_USER_ID  # the key's owner, whose session it is
        async with httpx.AsyncClient(base_url=server.url) as c:
            r = await c.post(
                f"/v1/sessions/{session_id}/ticket",
                json={"role": "producer"},
                headers={"Authorization": f"Bearer {token}"},
            )
        r.raise_for_status()
        return str(r.json()["ticket"])

    def make(**kw: object) -> StreamIngest:
        opts: dict[str, object] = {
            "stop_grace_s": 3.0,
            "poll_s": 0.1,
            "reconnect": ReconnectPolicy(initial_delay=0.05, max_delay=0.2),
        } | kw
        return StreamIngest(
            sm,
            paths,
            server_url=server.url,
            ticket_for=ticket_for,
            state_dir=tmp_path / "streams",
            reader_argv=clips.argv,
            **opts,  # type: ignore[arg-type]
        )

    return make


@pytest.fixture
async def running(make_ingest: Callable[..., StreamIngest]) -> AsyncIterator[StreamIngest]:
    ingest = make_ingest()
    task = asyncio.create_task(ingest.run())
    yield ingest
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await ingest.shutdown()


async def _sessions(sm: SM, title: str) -> list[SessionRow]:
    async with sm() as db:
        rows = await db.execute(select(SessionRow).where(SessionRow.title == title))
        return list(rows.scalars())


async def _one_session(sm: SM, title: str) -> SessionRow:
    found: list[SessionRow] = []

    async def poll() -> bool:
        found[:] = await _sessions(sm, title)
        return bool(found)

    await _eventually_async(poll)
    assert len(found) == 1
    return found[0]


async def _eventually_async(cond: Callable[[], object], timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if await cond():  # type: ignore[misc]
            return
        assert asyncio.get_running_loop().time() < deadline, "condition never became true"
        await asyncio.sleep(0.1)


async def _count(sm: SM, model: type, sid: uuid.UUID, **where: object) -> int:
    async with sm() as db:
        q = select(func.count()).select_from(model).where(model.session_id == sid)  # type: ignore[attr-defined]
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return int((await db.execute(q)).scalar_one())


async def _mic_seqs(sm: SM, sid: uuid.UUID) -> list[int]:
    async with sm() as db:
        rows = await db.execute(
            select(AudioChunk.seq)
            .where(AudioChunk.session_id == sid, AudioChunk.channel == "mic")
            .order_by(AudioChunk.seq)
        )
        return [r[0] for r in rows]


async def _status(sm: SM, sid: uuid.UUID) -> str:
    async with sm() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        return row.status


async def _ended(sm: SM, sid: uuid.UUID) -> bool:
    return await _status(sm, sid) != "live"


async def test_a_stream_becomes_a_live_session_with_the_keys_profile(
    sm: SM, paths: FakePaths, clips: Clips, running: StreamIngest
) -> None:
    await keys.create_key(
        sm, N["obs-desk"], OWNER_USER_ID, mode="meeting", title=T["obs profile"], restricted=True
    )
    clips.add(N["obs-desk"], 0)
    paths.online[N["obs-desk"]] = 2

    row = await _one_session(sm, T["obs profile"])
    assert (row.source, row.mode, row.restricted, row.status) == ("obs", "meeting", True, "live")
    assert row.channels == ["mic", "system"]
    assert row.device_id is None and row.user_id == OWNER_USER_ID

    # Both tracks become segments: the mic as "me", desktop audio as "them".
    async def both_channels_transcribed() -> bool:
        mic = await _count(sm, SegmentRow, row.id, channel="mic")
        system = await _count(sm, SegmentRow, row.id, channel="system")
        return mic >= 2 and system >= 1

    await _eventually_async(both_channels_transcribed)
    assert running.active[N["obs-desk"]].session_id == row.id


async def test_stream_stop_ends_the_session_after_the_grace_period(
    sm: SM, paths: FakePaths, clips: Clips, running: StreamIngest
) -> None:
    await keys.create_key(sm, N["stopper"], OWNER_USER_ID, title=T["stop test"])
    clips.add(N["stopper"], 0)
    paths.online[N["stopper"]] = 2
    row = await _one_session(sm, T["stop test"])
    await _eventually_async(lambda: _count(sm, SegmentRow, row.id, channel="mic"))

    del paths.online[N["stopper"]]  # OBS stopped
    await _eventually_async(lambda: _ended(sm, row.id))
    assert N["stopper"] not in running.active
    seqs = await _mic_seqs(sm, row.id)
    assert seqs == list(range(len(seqs))) and len(seqs) >= SECONDS * 10 - 2  # all of it stored
    await _eventually_async(lambda: _gone(running, row.id))  # the outbox is cleaned up


async def _gone(ingest: StreamIngest, sid: uuid.UUID) -> bool:
    return not (ingest.state_dir / str(sid)).exists()


async def test_a_drop_within_the_grace_period_resumes_the_same_session(
    sm: SM, paths: FakePaths, clips: Clips, make_ingest: Callable[..., StreamIngest]
) -> None:
    ingest = make_ingest(stop_grace_s=4.0)
    task = asyncio.create_task(ingest.run())
    try:
        await keys.create_key(sm, N["dropper"], OWNER_USER_ID, title=T["drop test"])
        clips.add(N["dropper"], 0)
        clips.add(N["dropper"], 1)  # what OBS sends after it reconnects
        paths.online[N["dropper"]] = 2
        row = await _one_session(sm, T["drop test"])
        await _eventually_async(lambda: _count(sm, SegmentRow, row.id, channel="mic"))

        # The network drops: the publisher is gone, then back before the grace period ends.
        del paths.online[N["dropper"]]
        # And the connection to the server drops too: the outbox resumes it.
        ingest.active[N["dropper"]].conn.abort_connection()
        await asyncio.sleep(1.5)
        assert N["dropper"] in ingest.active
        paths.online[N["dropper"]] = 2

        async def both_clips_stored() -> bool:
            return len(await _mic_seqs(sm, row.id)) >= 2 * SECONDS * 10 - 4

        await _eventually_async(both_clips_stored)
        assert len(await _sessions(sm, T["drop test"])) == 1  # still one session
        seqs = await _mic_seqs(sm, row.id)
        assert seqs == list(range(len(seqs)))  # nothing lost or doubled across both drops

        del paths.online[N["dropper"]]
        await _eventually_async(lambda: _ended(sm, row.id))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await ingest.shutdown()


async def test_coming_back_after_the_grace_period_is_a_new_session(
    sm: SM, paths: FakePaths, clips: Clips, running: StreamIngest
) -> None:
    await keys.create_key(sm, N["again"], OWNER_USER_ID, title=T["again test"])
    clips.add(N["again"], 0)
    clips.add(N["again"], 1)
    paths.online[N["again"]] = 2
    first = await _one_session(sm, T["again test"])
    del paths.online[N["again"]]
    await _eventually_async(lambda: _ended(sm, first.id))
    await _eventually_async(lambda: _not_active(running, N["again"]))

    paths.online[N["again"]] = 2

    async def two() -> bool:
        return len(await _sessions(sm, T["again test"])) == 2

    await _eventually_async(two)


async def _not_active(ingest: StreamIngest, name: str) -> bool:
    return name not in ingest.active


async def test_a_single_track_stream_is_mic_only(
    sm: SM, paths: FakePaths, clips: Clips, running: StreamIngest
) -> None:
    await keys.create_key(sm, N["phone"], OWNER_USER_ID, title=T["phone test"], source="mobile")
    clips.add(N["phone"], 0, tracks=1)
    paths.online[N["phone"]] = 1
    row = await _one_session(sm, T["phone test"])
    assert row.channels == ["mic"] and row.source == "mobile"
    await _eventually_async(lambda: _count(sm, SegmentRow, row.id, channel="mic"))


async def test_a_path_without_a_key_or_audio_starts_nothing(
    sm: SM, paths: FakePaths, running: StreamIngest
) -> None:
    await keys.create_key(sm, N["silent"], OWNER_USER_ID, title=T["silent test"])
    paths.online |= {N["no-such-key"]: 2, N["silent"]: 0}
    await asyncio.sleep(0.5)
    assert running.active == {}
    assert await _sessions(sm, T["silent test"]) == []


async def test_mediamtx_being_unreachable_ends_nothing(
    sm: SM, paths: FakePaths, clips: Clips, running: StreamIngest
) -> None:
    await keys.create_key(sm, N["steady"], OWNER_USER_ID, title=T["steady test"])
    clips.add(N["steady"], 0)
    paths.online[N["steady"]] = 2
    row = await _one_session(sm, T["steady test"])
    paths.fail = True
    await asyncio.sleep(4.0)  # longer than the grace period
    assert N["steady"] in running.active and await _status(sm, row.id) == "live"


async def test_has_room_up_to_max_streams(
    sm: SM, paths: FakePaths, clips: Clips, make_ingest: Callable[..., StreamIngest]
) -> None:
    ingest = make_ingest(max_streams=1)
    task = asyncio.create_task(ingest.run())
    try:
        assert ingest.has_room(N["one"])
        await keys.create_key(sm, N["one"], OWNER_USER_ID, title=T["room test"])
        clips.add(N["one"], 0)
        paths.online[N["one"]] = 2
        await _one_session(sm, T["room test"])
        assert ingest.has_room(N["one"])  # its own reconnect is always let in
        assert not ingest.has_room(N["two"])
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await ingest.shutdown()
