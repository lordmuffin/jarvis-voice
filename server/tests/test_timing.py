"""Per-session timing: persisted as the session runs, summarised in the final note."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.schema import CopilotDelta, NoteUpsert
from jarvis_live.db.models import OWNER_USER_ID, CopilotItem, SegmentRow
from jarvis_live.finalize.finalizer import Finalizer
from jarvis_live.finalize.render import render_note
from jarvis_live.finalize.timing import TimingSummary, load_timing, render_timing
from jarvis_live.llm.client import LLMError
from tests.audio import TWO_UTTERANCES
from tests.fakes import FakeLLM, FakeNotifier, add_segments, make_session, new_device
from tests.test_finalizer import NOTE, Vault
from tests.test_instrumentation import SlowSTT, copilot_rig, live_pipeline
from tests.test_render import LINES, META

SM = async_sessionmaker  # type: ignore[type-arg]


def summary(**kw: Any) -> TimingSummary:
    base: dict[str, Any] = {
        "segments": 40,
        "tier_counts": {"gpu": 40},
        "latency_s": [1.0, 1.5, 2.0, 2.5, 3.0],
        "stt_s": {"gpu": [0.8, 1.0, 1.1]},
        "cycles_ok": 10,
        "cycles_failed": 0,
        "roundtrip_s": [3.0, 4.0, 5.0],
        "staleness_s": [26.0, 28.0, 30.0],
    }
    base.update(kw)
    return TimingSummary(**base)


def text(t: TimingSummary, **kw: Any) -> str:
    return "\n".join(render_timing(t, Settings(), primary_tier="gpu", **kw))


# --- rendering -------------------------------------------------------------------------------


def test_a_healthy_session_says_so() -> None:
    out = text(summary(), stop_to_note_s=60)
    assert out.startswith("## Session timing")
    assert "within budget" in out
    assert "gpu 100%" in out


def test_a_slow_transcript_names_the_breach_and_its_limit() -> None:
    out = text(summary(latency_s=[1.0] * 5 + [9.0] * 20))
    assert "slow" in out.lower() and "9.0" in out and "5" in out
    assert "within budget" not in out


def test_a_slow_tier_is_named() -> None:
    out = text(summary(stt_s={"gpu": [6.0] * 10}))
    assert "gpu" in out.split("Verdict")[1].splitlines()[0]


def test_a_fallback_tier_serving_the_session_is_named() -> None:
    out = text(summary(tier_counts={"gpu": 10, "cpu": 30}))
    verdict = out.split("Verdict")[1].splitlines()[0]
    assert "cpu" in verdict and "75%" in verdict


def test_failed_copilot_cycles_and_a_slow_note_are_flagged() -> None:
    out = text(summary(cycles_ok=6, cycles_failed=4), stop_to_note_s=300)
    verdict = out.split("Verdict")[1].splitlines()[0]
    assert "copilot" in verdict.lower() and "note" in verdict.lower()


def test_missing_data_is_left_out_not_zeroed() -> None:
    out = text(summary(roundtrip_s=[], staleness_s=[], cycles_ok=0))
    assert "round trip" not in out.lower()


def test_the_section_sits_before_the_transcript_and_only_when_given() -> None:
    plain = render_note(NOTE, META, LINES)
    assert "Session timing" not in plain
    timed = render_note(NOTE, META, LINES, timing=["## Session timing", "", "x"])
    assert timed.index("## Session timing") < timed.index("## Transcript")


# --- persistence -----------------------------------------------------------------------------


async def test_segments_keep_how_long_stt_and_the_wait_took(sm: SM) -> None:
    pipeline = await live_pipeline(sm, SlowSTT(0.1), ["mic"])
    try:
        pipeline.feed("mic", TWO_UTTERANCES)
        await pipeline.finish()
    finally:
        await pipeline.close()
    async with sm() as db:
        rows = (
            (
                await db.execute(
                    select(SegmentRow).where(SegmentRow.session_id == pipeline.session_id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    assert all(r.stt_ms is not None and r.stt_ms >= 100 for r in rows)
    assert all(r.latency_ms is not None and r.latency_ms >= r.stt_ms for r in rows)


async def test_each_copilot_cycle_leaves_a_timing_row(sm: SM) -> None:
    llm = FakeLLM().queue("CopilotDelta", CopilotDelta(notes_upsert=[NoteUpsert(text="x")]))
    r = await copilot_rig(sm, llm)
    await r.segs(3)
    await r.loop.tick()
    llm.responses["CopilotDelta"] = [LLMError("down")]
    await r.segs(3)
    await r.loop.tick()
    async with sm() as db:
        rows = (
            (
                await db.execute(
                    select(CopilotItem.payload).where(
                        CopilotItem.session_id == r.sid, CopilotItem.kind == "timing"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [p["ok"] for p in rows] == [True, False]
    assert rows[0]["stale_s"] is not None and rows[1]["stale_s"] is None


async def test_load_timing_summarises_a_session_from_the_database(sm: SM) -> None:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id)
    async with sm() as db:
        for tier, stt, lat in [("gpu", 800, 1500), ("gpu", 1200, 2500), ("cpu", 3000, 4000)]:
            db.add(
                SegmentRow(
                    session_id=sid, channel="mic", speaker="me", start_ms=0, end_ms=1000,
                    text="t", source="final", stt_tier=tier, stt_ms=stt, latency_ms=lat,
                )
            )  # fmt: skip
        db.add(SegmentRow(  # a segment from before timing existed: counted, never timed
            session_id=sid, channel="mic", speaker="me", start_ms=0, end_ms=1000,
            text="old", source="final", stt_tier="gpu",
        ))  # fmt: skip
        for payload in (
            {"ok": True, "rt_s": 3.0, "stale_s": 27.0},
            {"ok": True, "rt_s": 5.0, "stale_s": 30.0},
            {"ok": False, "rt_s": 8.0, "stale_s": None},
        ):
            db.add(CopilotItem(session_id=sid, kind="timing", payload=payload))
        await db.commit()
        t = await load_timing(db, sid)
    assert t is not None
    assert t.segments == 4 and t.tier_counts == {"gpu": 3, "cpu": 1}
    assert sorted(t.latency_s) == [1.5, 2.5, 4.0]
    assert t.stt_s["gpu"] == [0.8, 1.2] and t.stt_s["cpu"] == [3.0]
    assert (t.cycles_ok, t.cycles_failed) == (2, 1)
    assert t.roundtrip_s == [3.0, 5.0] and t.staleness_s == [27.0, 30.0]


async def test_a_session_with_nothing_recorded_has_no_timing(sm: SM) -> None:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id)
    async with sm() as db:
        assert await load_timing(db, sid) is None


# --- in the note -----------------------------------------------------------------------------


async def test_the_final_note_carries_the_timing_section(sm: SM, tmp_path: Path) -> None:
    fin = Finalizer(
        Settings(outbox_dir=tmp_path / "outbox", timezone="UTC", whisper_tiers="gpu http://g/v1 m"),
        sm,
        Bus(),
        FakeLLM().queue("FinalNote", NOTE),
        Vault(),
        FakeNotifier(),
    )
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    sid = await make_session(
        sm, device_id, status="finalizing", ended_at=datetime(2026, 10, 9, 15, 12, tzinfo=UTC)
    )
    await add_segments(sm, sid, [(1000, 4000, "them", "Hello.")])
    async with sm() as db:
        seg = (
            await db.execute(select(SegmentRow).where(SegmentRow.session_id == sid))
        ).scalar_one()
        seg.stt_tier, seg.stt_ms, seg.latency_ms = "cpu", 9000, 12000  # a throttled fallback
        await db.commit()

    await fin.run(sid)

    (note,) = sorted((tmp_path / "outbox").glob("*.md"))
    body = note.read_text()
    assert "## Session timing" in body
    assert body.index("## Session timing") < body.index("## Transcript")
    verdict = body.split("Verdict")[1].splitlines()[0]
    assert "cpu" in verdict and "slow" in verdict.lower()
