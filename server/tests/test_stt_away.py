"""When no Whisper tier can transcribe (the gaming PC is away): the audio is kept, clients are
told, and the session is transcribed by itself once a tier is back."""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.schema import FinalNote
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.ingest.hub import IngestHub
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.replay import ReplayProducer
from jarvis_live.retention import purge_expired_audio
from jarvis_live.stt.backend import STTUnavailable, Transcription
from tests.audio import TWO_UTTERANCES
from tests.conftest import FakeSTT, ServerHandle
from tests.fakes import FakeClock, FakeLLM, FakeNotifier, add_segments, make_session, new_device
from tests.helpers import RawProducer, eventually, open_viewer
from tests.test_copilot_loop import FakeVault

SM = async_sessionmaker  # type: ignore[type-arg]


class AwaySTT(FakeSTT):
    """No tier answers until ``back`` is set; the first ``up_for`` segments are served."""

    def __init__(self, up_for: int = 0) -> None:
        super().__init__()
        self.tier = "gamingpc"
        self.back = False
        self.up_for = up_for
        self.refused = 0

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        if not self.back and self.calls >= self.up_for:
            self.refused += 1
            raise STTUnavailable("no tier")
        return await super().transcribe(pcm, prompt)

    def current_tier(self) -> str | None:
        return "gamingpc" if self.back else None


async def _detail(server: ServerHandle, rp: ReplayProducer, sid: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=server.url, headers=rp._auth) as c:
        body: dict[str, Any] = (await c.get(f"/v1/sessions/{sid}")).json()
        return body


async def _settled(server: ServerHandle, rp: ReplayProducer, sid: str) -> dict[str, Any]:
    for _ in range(400):
        body = await _detail(server, rp, sid)
        if body["status"] == "done" and body["processing"]["transcription"] != "running":
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"session did not settle: {body['status']}")


async def _pending(sm: SM, sid: str | uuid.UUID) -> bool:
    async with sm() as db:
        row = await db.get(SessionRow, uuid.UUID(str(sid)))
        assert row is not None
        return row.transcription_pending


async def _forget(sm: SM, sid: uuid.UUID) -> None:
    """The test database is shared: leave nothing for another test's watchdog to pick up."""
    async with sm() as db:
        await db.execute(
            update(SessionRow).where(SessionRow.id == sid).values(transcription_pending=False)
        )
        await db.commit()


async def test_no_tier_keeps_the_audio_shows_it_and_transcribes_it_later(
    make_server: Callable[..., ServerHandle], token: str, sm: SM, tmp_path: Path
) -> None:
    llm = FakeLLM().queue("FinalNote", FinalNote(title="Later", summary="Back again."))
    notifier = FakeNotifier()
    stt = AwaySTT()
    outbox = tmp_path / "outbox"
    server = make_server(
        stt=stt,
        llm=llm,
        notifier=notifier,
        outbox_dir=outbox,
        finalize_recover_max_age_s=0,
        status_interval_s=0.1,
        copilot_poll_s=0.1,
        watchdog_interval_s=0.05,
        stt_outage_notify_s=0.1,
    )
    rp = ReplayProducer(server.url, token, speed=50.0)
    sid = await rp.create_session("away")
    async with await open_viewer(rp, sid) as viewer:
        await rp.run(TWO_UTTERANCES, session_id=sid)
        await eventually(lambda: stt.refused >= 2)
        away = await _settled(server, rp, sid)

        # Kept: the recording is whole, and marked to be transcribed.
        pcm = server.settings.data_dir / "sessions" / sid / "mic.pcm"
        assert pcm.read_bytes() == TWO_UTTERANCES
        assert await _pending(sm, sid)
        assert away["audio"]["mic"] is True and away["segments"] == []

        # Shown: live status says no tier, one error says what happened, and the finished
        # session says it is queued, not done with nothing to show.
        assert {s["stt_tier"] for s in viewer.of_type("status")} == {None}
        (error,) = viewer.of_type("error")
        assert error["code"] == "stt_unavailable" and "kept" in error["message"]
        assert away["processing"]["transcription"] == "queued"
        assert away["processing"]["summary"] == "queued"

        # No page, and no note from nothing.
        await asyncio.sleep(0.3)
        assert notifier.sent == [] and llm.calls == [] and away["final_note"] is None

        # Later: nobody does anything but bring Whisper back.
        stt.back = True
        await eventually(lambda: len(viewer.of_type("final_note")) == 1)

    await eventually(lambda: len(notifier.sent) == 1)
    assert notifier.sent[0].title == "Jarvis: note ready"
    later = await _settled(server, rp, sid)
    assert [s["text"] for s in later["segments"]] == ["seg-1", "seg-2"]
    assert all(s["stt_tier"] == "gamingpc" for s in later["segments"])
    assert later["processing"]["transcription"] == "idle"
    assert not await _pending(sm, sid)
    assert "seg-1" in llm.calls[-1].user
    assert (outbox / later["final_note"]["path"]).exists()


async def test_a_tier_lost_mid_session_holds_the_note_until_all_of_it_is_transcribed(
    make_server: Callable[..., ServerHandle], token: str, sm: SM, tmp_path: Path
) -> None:
    llm = FakeLLM().queue("FinalNote", FinalNote(title="Whole", summary="All of it."))
    notifier = FakeNotifier()
    stt = AwaySTT(up_for=1)
    server = make_server(
        stt=stt,
        llm=llm,
        notifier=notifier,
        outbox_dir=tmp_path / "outbox",
        finalize_recover_max_age_s=0,
        status_interval_s=0.1,
        copilot_poll_s=30,
        watchdog_interval_s=0.05,
    )
    rp = ReplayProducer(server.url, token, speed=50.0)
    sid = await rp.create_session("mid-session")
    async with await open_viewer(rp, sid) as viewer:
        producer, _ = await RawProducer.connect(rp, sid)
        await producer.send_pcm(0, TWO_UTTERANCES)
        await eventually(lambda: stt.refused >= 1)
        await producer.send_json({"type": "end"})
        held = await _settled(server, rp, sid)
        assert [s["text"] for s in held["segments"]] == ["seg-1"]
        assert held["final_note"] is None and llm.calls == [] and notifier.sent == []
        stt.back = True
        await eventually(lambda: len(viewer.of_type("final_note")) == 1)
    later = await _settled(server, rp, sid)
    assert len(later["segments"]) == 2  # the first one again, and the one that was lost
    assert not await _pending(sm, sid)


def _orchestrator(
    sm: SM, tmp_path: Path, stt: FakeSTT, llm: FakeLLM, *, hub: Any = None
) -> tuple[Orchestrator, Settings]:
    cfg = Settings(outbox_dir=tmp_path / "outbox", data_dir=tmp_path, stt_backfill_retry_s=300)
    orch = Orchestrator(cfg, sm, Bus(), stt, llm, FakeVault(), FakeNotifier(), clock=FakeClock())
    orch.attach(hub or IngestHub(cfg, sm, Bus(), stt))
    return orch, cfg


async def _waiting(sm: SM, data_dir: Path, **kw: Any) -> uuid.UUID:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id, status="done", **kw)
    async with sm() as db:
        await db.execute(
            update(SessionRow).where(SessionRow.id == sid).values(transcription_pending=True)
        )
        await db.commit()
    d = data_dir / "sessions" / str(sid)
    d.mkdir(parents=True)
    (d / "mic.pcm").write_bytes(TWO_UTTERANCES)
    return sid


async def test_a_local_only_session_is_never_queued(sm: SM) -> None:
    from jarvis_live.ingest.store import mark_transcription_pending

    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id, local_only=True)
    await mark_transcription_pending(sm, sid)
    assert not await _pending(sm, sid)


async def test_a_local_only_session_never_leaves_even_if_it_was_marked(
    sm: SM, tmp_path: Path
) -> None:
    # The gate holds at the sweep too, whatever marked the row: a restored backup, a bad migration.
    stt, llm = FakeSTT(), FakeLLM()
    orch, _ = _orchestrator(sm, tmp_path, stt, llm)
    sid = await _waiting(sm, tmp_path, local_only=True)
    await orch.check_once()
    assert orch.job(sid) is None
    await orch.shutdown()
    assert stt.calls == 0 and llm.calls == []
    await _forget(sm, sid)


async def test_a_deleted_session_is_not_transcribed(sm: SM, tmp_path: Path) -> None:
    from datetime import UTC, datetime

    stt = FakeSTT()
    orch, _ = _orchestrator(sm, tmp_path, stt, FakeLLM())
    sid = await _waiting(sm, tmp_path)
    async with sm() as db:
        await db.execute(
            update(SessionRow).where(SessionRow.id == sid).values(deleted_at=datetime.now(UTC))
        )
        await db.commit()
    await orch.check_once()
    assert orch.job(sid) is None
    await orch.shutdown()
    await _forget(sm, sid)


async def test_nothing_is_transcribed_while_a_session_is_live(sm: SM, tmp_path: Path) -> None:
    # Whisper serves the live session first; the backlog waits for it to end.
    from types import SimpleNamespace

    stt = FakeSTT()
    live = SimpleNamespace(
        session_id=uuid.uuid4(),
        pipeline=SimpleNamespace(last_audio_at=0.0, lag_ms=lambda: 0),
        uploading=True,
        producer=None,
        producer_gone_at=None,
    )
    hub = SimpleNamespace(live_sessions=lambda: [live], end=None)
    orch, _ = _orchestrator(sm, tmp_path, stt, FakeLLM(), hub=hub)
    sid = await _waiting(sm, tmp_path)
    await orch.check_once()
    assert orch.job(sid) is None
    await orch.shutdown()
    await _forget(sm, sid)


async def test_a_backfill_that_fails_stays_queued_and_waits_before_trying_again(
    sm: SM, tmp_path: Path
) -> None:
    class Flapping(FakeSTT):
        async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
            self.calls += 1
            raise STTUnavailable("gone again")

    stt = Flapping()
    orch, _ = _orchestrator(sm, tmp_path, stt, FakeLLM())
    clock: Any = orch._clock
    sid = await _waiting(sm, tmp_path)
    await orch.check_once()
    await eventually(lambda: orch.job(sid) is None)
    assert await _pending(sm, sid)
    assert not orch.transcription_failed(sid)  # still queued: not a failure to act on
    tried = stt.calls
    await orch.check_once()
    assert orch.job(sid) is None and stt.calls == tried
    await clock.advance(301)
    await orch.check_once()
    await eventually(lambda: stt.calls > tried)
    await eventually(lambda: orch.job(sid) is None)
    await orch.shutdown()
    await _forget(sm, sid)


async def test_a_session_whose_audio_is_gone_leaves_the_queue_with_a_note(
    sm: SM, tmp_path: Path
) -> None:
    llm = FakeLLM().queue("FinalNote", FinalNote(title="From what there is", summary="S"))
    orch, _ = _orchestrator(sm, tmp_path, FakeSTT(), llm)
    sid = await _waiting(sm, tmp_path)
    await add_segments(sm, sid, [(0, 1000, "me", "what arrived")])
    (tmp_path / "sessions" / str(sid) / "mic.pcm").unlink()
    await orch.check_once()
    await eventually(lambda: orch.job(sid) is None)
    await orch.shutdown()
    assert not await _pending(sm, sid)
    assert "what arrived" in llm.calls[-1].user


async def test_retention_keeps_audio_still_waiting_to_be_transcribed(
    sm: SM, tmp_path: Path
) -> None:
    sid = await _waiting(sm, tmp_path)
    await purge_expired_audio(sm, tmp_path, 0, now=9_999_999_999.0)
    assert (tmp_path / "sessions" / str(sid) / "mic.pcm").exists()
    await _forget(sm, sid)
    await purge_expired_audio(sm, tmp_path, 0, now=9_999_999_999.0)
    assert not (tmp_path / "sessions" / str(sid) / "mic.pcm").exists()


async def test_a_failed_retranscribe_of_a_finished_session_does_not_queue_it(
    sm: SM, tmp_path: Path
) -> None:
    # Pressing "process" while Whisper is away keeps the old transcript: nothing to replace later.
    import pytest

    from jarvis_live.ingest.retranscribe import RetranscribeFailed, retranscribe

    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id, status="finalizing")
    d = tmp_path / "sessions" / str(sid)
    d.mkdir(parents=True)
    (d / "mic.pcm").write_bytes(TWO_UTTERANCES)
    with pytest.raises(RetranscribeFailed):
        await retranscribe(
            sid,
            ["mic"],
            settings=Settings(data_dir=tmp_path),
            sessionmaker=sm,
            bus=Bus(),
            stt=AwaySTT(),
        )
    assert not await _pending(sm, sid)
    assert (await _status(sm, sid)) == "finalizing"


async def test_transcribing_kept_audio_says_nothing_about_live_latency(
    sm: SM, tmp_path: Path, monkeypatch: Any
) -> None:
    # A backlog waits in a queue by design. Counted, it would trip "Transcript slow" for the next
    # live session, a page caused by Whisper coming back.
    from jarvis_live import metrics
    from jarvis_live.ingest.retranscribe import retranscribe

    seen: list[float] = []
    monkeypatch.setattr(metrics, "authoritative", lambda *a, **k: seen.append(a[1]))
    sid = await _waiting(sm, tmp_path)
    await retranscribe(
        sid,
        ["mic"],
        settings=Settings(data_dir=tmp_path),
        sessionmaker=sm,
        bus=Bus(),
        stt=FakeSTT(),
    )
    assert seen == []
    await _forget(sm, sid)


async def _status(sm: SM, sid: uuid.UUID) -> str:
    async with sm() as db:
        return str(
            (await db.execute(select(SessionRow.status).where(SessionRow.id == sid))).scalar()
        )
