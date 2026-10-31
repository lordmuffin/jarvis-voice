"""The real path records the metrics the SLOs and the dashboard are built on."""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live import metrics
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.loop import CopilotLoop
from jarvis_live.copilot.schema import CopilotDelta, NoteUpsert
from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.finalize.finalizer import Finalizer
from jarvis_live.ingest.pipeline import SessionPipeline
from jarvis_live.llm.client import LLMError
from jarvis_live.protocol import DraftSegment
from jarvis_live.stt.backend import Transcription
from jarvis_live.stt.tiers import STTUnavailable, TierPool, parse_tiers
from tests.audio import TWO_UTTERANCES
from tests.fakes import FakeClock, FakeLLM, FakeNotifier, add_segments, make_session, new_device
from tests.helpers import eventually
from tests.test_copilot_loop import FakeVault
from tests.test_finalizer import NOTE, Vault
from tests.test_stt import SPEC, FakeWhisper

SM = async_sessionmaker  # type: ignore[type-arg]


def val(name: str, **labels: str) -> float:
    return metrics.sample(name, labels) or 0.0


class Delta:
    """Change in a sample since this was created."""

    def __init__(self, name: str, **labels: str) -> None:
        self.name, self.labels = name, labels
        self.base = val(name, **labels)

    def __call__(self) -> float:
        return val(self.name, **self.labels) - self.base


# --- STT tiers -------------------------------------------------------------------------------


class Ticker:
    """A clock the fake Whisper advances, so request times are exact and nothing sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def tier_pool(fake: FakeWhisper, ticker: Ticker, took: dict[str, float]) -> TierPool:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            ticker.now += took[request.url.host.split(".")[0]]
        return fake(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TierPool(parse_tiers(SPEC), client, clock=ticker)


async def test_a_served_request_is_timed_and_counted_for_its_tier() -> None:
    served, count, total = (
        Delta("jarvis_live_stt_served_total", tier="gpu"),
        Delta("jarvis_live_stt_request_seconds_count", tier="gpu", outcome="ok"),
        Delta("jarvis_live_stt_request_seconds_sum", tier="gpu", outcome="ok"),
    )
    pool = tier_pool(FakeWhisper(gpu={}, cpu={}), Ticker(), {"gpu": 1.5, "cpu": 9})
    await pool.transcribe(b"wav", "")
    assert (served(), count(), total()) == (1, 1, 1.5)


async def test_a_failing_tier_is_a_fallthrough_and_an_error_not_a_serve() -> None:
    fell = Delta("jarvis_live_stt_fallthrough_total", tier="gpu")
    err = Delta("jarvis_live_stt_request_seconds_count", tier="gpu", outcome="error")
    served_cpu = Delta("jarvis_live_stt_served_total", tier="cpu")
    pool = tier_pool(FakeWhisper(gpu={"transcribe": 500}, cpu={}), Ticker(), {"gpu": 0, "cpu": 0})
    await pool.transcribe(b"wav", "")
    assert (fell(), err(), served_cpu()) == (1, 1, 1)


async def test_a_timeout_is_its_own_outcome() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json={"data": []})

    timeouts = Delta("jarvis_live_stt_request_seconds_count", tier="gpu", outcome="timeout")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pool = TierPool(parse_tiers(SPEC), client)
    with pytest.raises(STTUnavailable):  # both tiers time out
        await pool.transcribe(b"wav", "")
    assert timeouts() == 1


async def test_probing_publishes_tier_health_and_the_tier_in_use() -> None:
    pool = tier_pool(FakeWhisper(gpu={"models": 503, "health": 503}, cpu={}), Ticker(), {})
    await pool.probe_all()
    assert val("jarvis_live_stt_tier_healthy", tier="gpu") == 0
    assert val("jarvis_live_stt_tier_healthy", tier="cpu") == 1
    assert val("jarvis_live_stt_current_tier", tier="cpu") == 1
    assert val("jarvis_live_stt_current_tier", tier="gpu") == 0


# --- the ingest pipeline ---------------------------------------------------------------------


class SlowSTT:
    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s, self.calls = delay_s, 0

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return Transcription(text=f"seg-{self.calls}", tier="fake")

    def current_tier(self) -> str | None:
        return "fake"


async def live_pipeline(sm: SM, stt: Any, channels: list[str]) -> SessionPipeline:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id)
    pipeline = SessionPipeline(
        sid, channels, settings=Settings(), sessionmaker=sm, bus=Bus(), stt=stt
    )
    pipeline.start()
    return pipeline


async def test_authoritative_time_runs_from_segment_cut_to_published(sm: SM) -> None:
    count = Delta("jarvis_live_authoritative_seconds_count", channel="mic")
    total = Delta("jarvis_live_authoritative_seconds_sum", channel="mic")
    seg_len = Delta("jarvis_live_segment_seconds_count", channel="mic")
    pipeline = await live_pipeline(sm, SlowSTT(0.1), ["mic"])
    try:
        pipeline.feed("mic", TWO_UTTERANCES)
        await pipeline.finish()
    finally:
        await pipeline.close()
    assert count() == 2 and seg_len() == 2
    assert total() >= 0.2  # two segments, each waited 0.1 s on STT (they queue in series)


async def test_a_held_mic_segment_records_how_long_it_waited(sm: SM) -> None:
    held = Delta("jarvis_live_dup_hold_seconds_count")
    pipeline = await live_pipeline(sm, SlowSTT(), ["mic", "system"])
    try:
        pipeline.feed("mic", TWO_UTTERANCES)  # system stays silent and unsettled
        await eventually(lambda: pipeline.pending_segments() == 0)
        await pipeline.finish()  # releases what is held
    finally:
        await pipeline.close()
    assert held() >= 1


async def test_drafts_record_first_partial_cadence_and_lag(sm: SM) -> None:
    first = Delta("jarvis_live_draft_first_seconds_count")
    gap = Delta("jarvis_live_draft_interval_seconds_count", channel="mic")
    lag = Delta("jarvis_live_draft_lag_seconds_sum", channel="mic")
    pipeline = await live_pipeline(sm, SlowSTT(), ["mic"])
    try:
        pipeline.feed("mic", TWO_UTTERANCES)  # 7.68 s of stream
        for end in (1000, 2000, 3000):
            pipeline.on_draft(
                DraftSegment(channel="mic", start_ms=0, end_ms=end, text="hel", final=False)
            )
        pipeline.on_draft(
            DraftSegment(channel="mic", start_ms=0, end_ms=3000, text="hello", final=True)
        )
    finally:
        await pipeline.close()
    assert first() == 1  # only the first partial of the session
    assert gap() == 2  # three partials, two gaps; the final draft is not a partial
    assert lag() > 0  # the stream is ahead of every draft


# --- copilot ---------------------------------------------------------------------------------


class Rig:
    def __init__(self, sm: Any, sid: uuid.UUID, llm: FakeLLM) -> None:
        self.sm, self.sid, self.llm, self.clock = sm, sid, llm, FakeClock()
        self.stream_ms = 0
        self.loop = CopilotLoop(
            sid,
            settings=Settings(),
            sessionmaker=sm,
            bus=Bus(),
            llm=llm,
            vault=FakeVault(),
            stream_ms=lambda: self.stream_ms,
            clock=self.clock,
        )

    async def segs(self, n: int) -> None:
        start = self.stream_ms
        await add_segments(
            self.sm,
            self.sid,
            [(start + i * 1000, start + i * 1000 + 900, "me", "x") for i in range(n)],
        )
        self.stream_ms = start + n * 1000


async def copilot_rig(sm: SM, llm: FakeLLM) -> Rig:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    r = Rig(sm, await make_session(sm, device_id), llm)
    await r.loop.tick()
    return r


async def test_copilot_round_trip_and_staleness(sm: SM) -> None:
    llm = FakeLLM().queue("CopilotDelta", CopilotDelta(notes_upsert=[NoteUpsert(text="a point")]))
    r = await copilot_rig(sm, llm)
    real = llm.complete_json

    async def took_3s(**kw: Any) -> Any:
        r.clock.now += 3
        return await real(**kw)

    llm.complete_json = took_3s  # type: ignore[method-assign]
    rt = Delta("jarvis_live_copilot_roundtrip_seconds_sum", outcome="ok")
    stale = Delta("jarvis_live_copilot_staleness_seconds_sum")
    stale_n = Delta("jarvis_live_copilot_staleness_seconds_count")
    await r.segs(1)
    await r.loop.tick()  # first sight: the 25 s timer starts
    await r.clock.advance(25)
    await r.loop.tick()  # cycle: LLM takes 3 s, so the snapshot lands 28 s after the segment
    assert rt() == 3
    assert (stale_n(), stale()) == (1, 28)


async def test_a_failed_cycle_counts_as_an_error_and_not_as_stale(sm: SM) -> None:
    llm = FakeLLM().queue("CopilotDelta", LLMError("down"))
    r = await copilot_rig(sm, llm)
    err = Delta("jarvis_live_copilot_roundtrip_seconds_count", outcome="error")
    stale_n = Delta("jarvis_live_copilot_staleness_seconds_count")
    await r.segs(3)
    await r.loop.tick()
    assert (err(), stale_n()) == (1, 0)


# --- finalizer -------------------------------------------------------------------------------


async def finalizer_run(sm: SM, tmp_path: Path, llm: FakeLLM, **kw: Any) -> Any:
    fin = Finalizer(
        Settings(outbox_dir=tmp_path / "outbox", timezone="UTC"),
        sm,
        Bus(),
        llm,
        Vault(),
        FakeNotifier(),
    )
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    sid = await make_session(
        sm, device_id, status="finalizing", ended_at=datetime(2026, 10, 9, 15, 12, tzinfo=UTC)
    )
    await add_segments(sm, sid, [(1000, 4000, "them", "Hello."), (5000, 8000, "me", "Bye.")])
    return await fin.run(sid, **kw)


async def test_a_finished_note_records_its_duration_by_source(sm: SM, tmp_path: Path) -> None:
    n = Delta("jarvis_live_finalize_seconds_count", source="regenerate", outcome="ok")
    await finalizer_run(sm, tmp_path, FakeLLM().queue("FinalNote", NOTE), source="regenerate")
    assert n() == 1


async def test_a_failed_note_records_an_error_outcome(sm: SM, tmp_path: Path) -> None:
    n = Delta("jarvis_live_finalize_seconds_count", source="finalize", outcome="error")
    await finalizer_run(sm, tmp_path, FakeLLM().queue("FinalNote", LLMError("down")))
    assert n() == 1
