import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.stt.backend import Transcription
from tests.conftest import FakeSTT
from tests.fakes import FakeClock, FakeLLM, FakeNotifier, make_session, new_device
from tests.helpers import eventually
from tests.test_copilot_loop import FakeVault

SM = async_sessionmaker  # type: ignore[type-arg]


@dataclass
class StubHub:
    states: list[Any]
    ended: list[uuid.UUID]

    def live_sessions(self) -> list[Any]:
        return self.states

    async def end(self, session_id: uuid.UUID) -> bool:
        self.ended.append(session_id)
        self.states = [s for s in self.states if s.session_id != session_id]
        return True


def _state(clock: FakeClock, idle_for: float = 0.0, uploading: bool = False) -> Any:
    return SimpleNamespace(
        session_id=uuid.uuid4(),
        pipeline=SimpleNamespace(last_audio_at=clock.now - idle_for, lag_ms=lambda: 0),
        uploading=uploading,
    )


@pytest.fixture
def rig(sm: SM, tmp_path: Path) -> Any:
    clock = FakeClock()
    stt = FakeSTT()
    notifier = FakeNotifier()
    orch = Orchestrator(
        Settings(outbox_dir=tmp_path, public_base_url="https://j.example"),
        sm,
        Bus(),
        stt,
        FakeLLM(),
        FakeVault(),
        notifier,
        clock=clock,
    )
    hub = StubHub([], [])
    orch.attach(hub)  # type: ignore[arg-type]
    return SimpleNamespace(orch=orch, clock=clock, stt=stt, notifier=notifier, hub=hub)


class NoTier(FakeSTT):
    def current_tier(self) -> str | None:
        return None


async def test_idle_session_is_ended_after_ten_minutes(rig: Any) -> None:
    quiet, busy = _state(rig.clock, idle_for=601), _state(rig.clock, idle_for=599)
    rig.hub.states = [quiet, busy]
    await rig.orch.check_once()
    assert rig.hub.ended == [quiet.session_id]


async def test_uploaded_session_is_not_ended_as_idle_while_it_is_transcribed(rig: Any) -> None:
    # An upload feeds all its audio at once; transcribing it can take longer than idle_end_s.
    upload = _state(rig.clock, idle_for=3600, uploading=True)
    rig.hub.states = [upload]
    await rig.orch.check_once()
    assert rig.hub.ended == []


async def test_live_session_without_producer_is_ended_after_idle_period(rig: Any, sm: SM) -> None:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id)  # live in the DB, nothing in the hub
    await rig.orch.check_once()
    await rig.clock.advance(599)
    await rig.orch.check_once()
    assert sid not in rig.hub.ended
    await rig.clock.advance(2)
    await rig.orch.check_once()
    assert sid in rig.hub.ended


async def test_stt_outage_notifies_once_after_two_minutes(rig: Any) -> None:
    rig.orch._stt = NoTier()
    rig.hub.states = [_state(rig.clock)]
    await rig.orch.check_once()
    await rig.clock.advance(119)
    rig.hub.states[0].pipeline.last_audio_at = rig.clock.now
    await rig.orch.check_once()
    assert rig.notifier.sent == []
    await rig.clock.advance(2)
    rig.hub.states[0].pipeline.last_audio_at = rig.clock.now
    await rig.orch.check_once()
    assert [s.priority for s in rig.notifier.sent] == [8]
    for _ in range(3):  # same outage: no repeats
        await rig.clock.advance(60)
        rig.hub.states[0].pipeline.last_audio_at = rig.clock.now
        await rig.orch.check_once()
    assert len(rig.notifier.sent) == 1
    # recovery re-arms the alert
    rig.orch._stt = FakeSTT()
    await rig.orch.check_once()
    rig.orch._stt = NoTier()
    await rig.orch.check_once()
    await rig.clock.advance(121)
    rig.hub.states[0].pipeline.last_audio_at = rig.clock.now
    await rig.orch.check_once()
    assert len(rig.notifier.sent) == 2


async def test_no_outage_alert_without_a_live_session(rig: Any) -> None:
    rig.orch._stt = NoTier()
    await rig.orch.check_once()
    await rig.clock.advance(600)
    await rig.orch.check_once()
    assert rig.notifier.sent == []


async def test_recover_finalizes_recent_finalizing_sessions_only(rig: Any, sm: SM) -> None:
    from datetime import UTC, datetime, timedelta

    from jarvis_live.copilot.schema import FinalNote
    from tests.fakes import add_segments

    rig.orch._finalizer._llm = FakeLLM().queue("FinalNote", FinalNote(title="T", summary="S"))
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    now = datetime.now(UTC)
    recent = await make_session(sm, device_id, status="finalizing", ended_at=now)
    stale = await make_session(sm, device_id, status="finalizing", ended_at=now - timedelta(days=3))
    for sid in (recent, stale):
        await add_segments(sm, sid, [(0, 1000, "me", "hi")])

    await rig.orch.recover()
    await eventually(lambda: any(str(recent) in (s.url or "") for s in rig.notifier.sent))
    await rig.orch.shutdown()
    assert not any(str(stale) in (s.url or "") for s in rig.notifier.sent)


async def test_on_final_runs_after_a_note_and_its_errors_are_contained(
    sm: SM, tmp_path: Path
) -> None:
    from jarvis_live.copilot.schema import FinalNote
    from tests.fakes import add_segments

    called: list[uuid.UUID] = []

    async def hook(sid: uuid.UUID) -> None:
        called.append(sid)
        raise RuntimeError("GitHub is down")

    llm = FakeLLM().queue("FinalNote", FinalNote(title="T", summary="S"))
    orch = Orchestrator(
        Settings(outbox_dir=tmp_path),
        sm,
        Bus(),
        FakeSTT(),
        llm,
        FakeVault(),
        FakeNotifier(),
        on_final=hook,
    )
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    written = await make_session(sm, device_id, status="finalizing")
    await add_segments(sm, written, [(0, 1000, "me", "hi")])
    empty = await make_session(sm, device_id, status="finalizing")  # no transcript: no note

    orch.regenerate(written, None)
    orch.on_end(empty)
    assert orch.is_finalizing(written)
    await eventually(lambda: not orch.is_finalizing(written) and not orch.is_finalizing(empty))
    await orch.shutdown()
    assert called == [written]


class GatedSTT(FakeSTT):
    """Holds every transcription until ``gate`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        await self.gate.wait()
        return await super().transcribe(pcm, prompt)


async def _finished_with_audio(sm: SM, data_dir: Path, status: str = "finalizing") -> uuid.UUID:
    from tests.audio import TWO_UTTERANCES
    from tests.fakes import add_segments

    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    sid = await make_session(sm, device_id, status=status)
    await add_segments(sm, sid, [(0, 1000, "me", "old words")])
    d = data_dir / "sessions" / str(sid)
    d.mkdir(parents=True)
    (d / "mic.pcm").write_bytes(TWO_UTTERANCES)
    return sid


async def _status(sm: SM, sid: uuid.UUID) -> str:
    from jarvis_live.db.models import Session as SessionRow

    async with sm() as db:
        row = await db.get(SessionRow, sid, populate_existing=True)
        assert row is not None
        return row.status


async def test_reprocess_transcribes_the_audio_again_then_writes_the_note(
    sm: SM, tmp_path: Path
) -> None:
    from jarvis_live.copilot.schema import FinalNote

    stt = GatedSTT()
    llm = FakeLLM().queue("FinalNote", FinalNote(title="T", summary="S"))
    orch = Orchestrator(
        Settings(outbox_dir=tmp_path / "outbox", data_dir=tmp_path),
        sm,
        Bus(),
        stt,
        llm,
        FakeVault(),
        FakeNotifier(),
    )
    sid = await _finished_with_audio(sm, tmp_path)

    orch.reprocess(sid, None, restore="done")
    job = orch.job(sid)
    assert job is not None and (job.source, job.stage) == ("reprocess", "transcription")
    assert orch.is_finalizing(sid)
    stt.gate.set()
    await eventually(lambda: orch.job(sid) is None)
    await orch.shutdown()
    assert await _status(sm, sid) == "done"
    assert "seg-1" in llm.calls[-1].user and "old words" not in llm.calls[-1].user
    assert not orch.transcription_failed(sid)


async def test_reprocess_that_cannot_transcribe_keeps_the_note_and_says_so(
    sm: SM, tmp_path: Path
) -> None:
    from jarvis_live.stt.backend import STTUnavailable

    class DownSTT(FakeSTT):
        async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
            raise STTUnavailable("down")

    llm = FakeLLM()
    orch = Orchestrator(
        Settings(outbox_dir=tmp_path / "outbox", data_dir=tmp_path),
        sm,
        Bus(),
        DownSTT(),
        llm,
        FakeVault(),
        FakeNotifier(),
    )
    sid = await _finished_with_audio(sm, tmp_path)
    orch.reprocess(sid, None, restore="done")
    await eventually(lambda: orch.job(sid) is None)
    await orch.shutdown()
    assert orch.transcription_failed(sid)
    assert await _status(sm, sid) == "done"  # the earlier note still stands
    assert llm.calls == []


async def test_a_finalizer_run_is_a_summary_job(sm: SM, tmp_path: Path) -> None:
    from jarvis_live.copilot.schema import FinalNote

    llm = FakeLLM(delay_s=0.2).queue("FinalNote", FinalNote(title="T", summary="S"))
    orch = Orchestrator(
        Settings(outbox_dir=tmp_path), sm, Bus(), FakeSTT(), llm, FakeVault(), FakeNotifier()
    )
    sid = await _finished_with_audio(sm, tmp_path / "data")
    orch.on_end(sid)
    job = orch.job(sid)
    assert job is not None and (job.source, job.stage) == ("finalize", "summary")
    await eventually(lambda: orch.job(sid) is None)
    await orch.shutdown()
