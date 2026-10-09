import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.auth import create_device
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.orchestrator import Orchestrator
from tests.conftest import FakeSTT
from tests.fakes import FakeClock, FakeLLM, FakeNotifier, make_session
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


def _state(clock: FakeClock, idle_for: float = 0.0) -> Any:
    return SimpleNamespace(
        session_id=uuid.uuid4(), pipeline=SimpleNamespace(last_audio_at=clock.now - idle_for)
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


async def test_live_session_without_producer_is_ended_after_idle_period(rig: Any, sm: SM) -> None:
    device_id, _ = await create_device(sm, f"dev-{uuid.uuid4()}")
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
    device_id, _ = await create_device(sm, f"dev-{uuid.uuid4()}")
    now = datetime.now(UTC)
    recent = await make_session(sm, device_id, status="finalizing", ended_at=now)
    stale = await make_session(sm, device_id, status="finalizing", ended_at=now - timedelta(days=3))
    for sid in (recent, stale):
        await add_segments(sm, sid, [(0, 1000, "me", "hi")])

    await rig.orch.recover()
    await eventually(lambda: any(str(recent) in (s.url or "") for s in rig.notifier.sent))
    await rig.orch.shutdown()
    assert not any(str(stale) in (s.url or "") for s in rig.notifier.sent)
