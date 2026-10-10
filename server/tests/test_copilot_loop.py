import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.auth import create_device
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.loop import CopilotLoop
from jarvis_live.copilot.schema import ActionUpsert, CopilotDelta, NoteUpsert, SuggestionIn
from jarvis_live.db.models import CopilotItem
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.llm.client import LLMError
from jarvis_live.protocol import Copilot, Related, Status
from tests.fakes import FakeClock, FakeLLM, add_segments, make_session

SM = async_sessionmaker  # type: ignore[type-arg]


class FakeVault:
    def __init__(self, hits: list[Related] | None = None) -> None:
        self.hits = hits or []
        self.searches: list[tuple[list[str], set[str], int]] = []

    async def search(self, topics: list[str], exclude: set[str], limit: int) -> list[Related]:
        self.searches.append((topics, set(exclude), limit))
        return [h for h in self.hits if h.path not in exclude][:limit]

    async def resolve_name(self, name: str) -> str | None:
        return None


class Rig:
    def __init__(self, sm: Any, sid: uuid.UUID, llm: FakeLLM, vault: FakeVault) -> None:
        self.sm, self.sid, self.llm, self.vault = sm, sid, llm, vault
        self.clock = FakeClock()
        self.bus = Bus()
        self.stream_ms = 0
        self.llm_ok_calls: list[bool] = []
        self.events: list[Any] = []
        self.sub = self.bus.subscribe(str(sid))
        self.loop = self.make()

    def make(self) -> CopilotLoop:
        return CopilotLoop(
            self.sid,
            settings=Settings(),
            sessionmaker=self.sm,
            bus=self.bus,
            llm=self.llm,
            vault=self.vault,
            stream_ms=lambda: self.stream_ms,
            set_llm_ok=self.llm_ok_calls.append,
            clock=self.clock,
        )

    async def segs(self, n: int, start: int | None = None) -> None:
        start = self.stream_ms if start is None else start
        await add_segments(
            self.sm,
            self.sid,
            [
                (start + i * 1000, start + i * 1000 + 900, "me", f"line {start}-{i}")
                for i in range(n)
            ],
        )
        self.stream_ms = start + n * 1000

    def published(self) -> list[Any]:
        out = []
        while not self.sub._queue.empty():
            item = self.sub._queue.get_nowait()
            out.append(item)
        return out


@pytest.fixture
async def rig(sm: SM) -> Rig:
    device_id, _ = await create_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id)
    llm = FakeLLM().queue("CopilotDelta", CopilotDelta())
    r = Rig(sm, sid, llm, FakeVault())
    await r.loop.tick()  # restore (nothing yet)
    return r


async def test_runs_after_three_new_segments(rig: Rig) -> None:
    await rig.segs(2)
    await rig.loop.tick()
    assert not rig.llm.calls
    await rig.segs(1)
    await rig.loop.tick()
    assert len(rig.llm.calls) == 1
    await rig.loop.tick()  # nothing new since
    assert len(rig.llm.calls) == 1


async def test_runs_after_25s_with_one_new_segment(rig: Rig) -> None:
    await rig.segs(1)
    await rig.loop.tick()  # first sight of the new segment starts the timer
    await rig.clock.advance(24)
    await rig.loop.tick()
    assert not rig.llm.calls
    await rig.clock.advance(1)
    await rig.loop.tick()
    assert len(rig.llm.calls) == 1


async def test_timer_does_not_run_without_new_segments(rig: Rig) -> None:
    await rig.clock.advance(300)
    await rig.loop.tick()
    assert not rig.llm.calls


async def test_never_concurrent_and_cancels_on_session_end(rig: Rig) -> None:
    rig.llm.delay_s = 0.05
    task = asyncio.create_task(rig.loop.run())
    await asyncio.sleep(0)  # let run() reach its first sleep
    for _ in range(4):
        await rig.segs(3)
        await rig.clock.advance(1)
        await asyncio.sleep(0.02)  # a cycle is mid-flight; more segments keep arriving
    await asyncio.sleep(0.2)
    for _ in range(3):
        await rig.clock.advance(1)
    assert rig.llm.max_active == 1 and rig.llm.calls
    # cancel mid-LLM-call
    await rig.segs(3)
    await rig.clock.advance(1)
    await asyncio.sleep(0.01)
    assert rig.llm.active == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.llm.active == 0


async def test_cycle_persists_and_publishes_full_snapshot(rig: Rig, sm: SM) -> None:
    rig.llm.queue(
        "CopilotDelta",
        CopilotDelta(
            notes_upsert=[NoteUpsert(text="Kickoff")],
            actions_upsert=[ActionUpsert(text="Send deck", owner="sam")],
            suggestions=[SuggestionIn(kind="question", text="Who owns QA?", ttl_s=90)],
            topics=["qa ownership"],
        ),
    )
    rig.llm.responses["CopilotDelta"].pop(0)  # drop the empty default
    rel = Related(path="P/QA.md", title="QA", snippet="s", uri="obsidian://x")
    rig.vault.hits = [rel]
    rig.stream_ms = 10_000
    await rig.segs(3, start=10_000)
    await rig.loop.tick()

    snaps = [e for e in rig.published() if isinstance(e, Copilot)]
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.version >= 1
    assert [(n.id, n.text) for n in snap.notes] == [("n1", "Kickoff")]
    assert snap.actions[0].owner == "sam"
    assert snap.suggestions[0].expires_at_ms == 13_000 + 90_000
    assert snap.related == [rel]
    async with sm() as db:
        row = (
            (
                await db.execute(
                    select(CopilotItem.payload)
                    .where(CopilotItem.session_id == rig.sid, CopilotItem.kind == "snapshot")
                    .order_by(CopilotItem.id.desc())
                )
            )
            .scalars()
            .first()
        )
    assert Copilot.model_validate(row) == snap
    assert rig.vault.searches == [(["qa ownership"], set(), 3)]


async def test_related_not_shown_twice_in_a_session(rig: Rig) -> None:
    a = Related(path="A.md", title="A", snippet="", uri="u")
    b = Related(path="B.md", title="B", snippet="", uri="u")
    rig.vault.hits = [a, b]
    rig.llm.responses["CopilotDelta"] = [CopilotDelta(topics=["t"])]
    await rig.segs(3)
    await rig.loop.tick()
    await rig.segs(3)
    await rig.loop.tick()
    assert rig.vault.searches[1][1] == {"A.md", "B.md"}


async def test_unchanged_delta_publishes_nothing(rig: Rig) -> None:
    await rig.segs(3)
    await rig.loop.tick()
    assert not [e for e in rig.published() if isinstance(e, Copilot)]


async def test_suggestions_expire_and_republish(rig: Rig) -> None:
    rig.llm.responses["CopilotDelta"] = [
        CopilotDelta(suggestions=[SuggestionIn(kind="gap", text="g", ttl_s=10)])
    ]
    await rig.segs(3)
    await rig.loop.tick()
    first = [e for e in rig.published() if isinstance(e, Copilot)][-1]
    assert [s.text for s in first.suggestions] == ["g"]
    rig.stream_ms += 10_001
    await rig.loop.tick()
    later = [e for e in rig.published() if isinstance(e, Copilot)]
    assert later[-1].suggestions == [] and later[-1].version > first.version


async def test_llm_failure_skips_cycle_and_flags_status(rig: Rig) -> None:
    rig.llm.responses["CopilotDelta"] = [LLMError("bad"), CopilotDelta(topics=[])]
    await rig.segs(3)
    await rig.loop.tick()
    assert rig.llm_ok_calls == [False]
    assert not [e for e in rig.published() if isinstance(e, Copilot)]
    await rig.loop.tick()  # failed cycle consumed its segments: no hot retry loop
    assert len(rig.llm.calls) == 1
    await rig.segs(3)
    await rig.loop.tick()
    assert rig.llm_ok_calls == [False, True]


async def test_prompt_contents(rig: Rig) -> None:
    sm = rig.sm
    await add_segments(
        sm,
        rig.sid,
        [
            (0, 900, "them", "ancient history"),  # outside the 8-minute window
            (600_000, 601_000, "me", "hello there"),
            (601_500, 603_000, "them", "hi back"),
        ],
    )
    await add_segments(
        sm,
        rig.sid,
        [
            (0, 500, "me", "old draft"),  # outside the window
            (600_100, 600_900, "me", "draft covered by final"),  # a final covers this range
            (700_000, 702_000, "them", "draft tail"),  # not finalized yet: fallback
        ],
        source="draft",
    )
    rig.stream_ms = 702_000
    await rig.loop.tick()  # 3 new finals -> cycle
    (call,) = rig.llm.calls
    assert call.model == "fast"
    assert call.system.startswith("/no_think")
    assert "ancient history" not in call.user and "old draft" not in call.user
    assert "draft covered by final" not in call.user
    assert "[10:00] me: hello there" in call.user
    assert "[10:01] them: hi back" in call.user
    assert "[11:40] them: draft tail" in call.user
    assert call.user.index("hello there") < call.user.index("draft tail")


async def test_restart_resumes_version_and_ids(rig: Rig) -> None:
    rig.llm.responses["CopilotDelta"] = [CopilotDelta(notes_upsert=[NoteUpsert(text="first")])]
    await rig.segs(3)
    await rig.loop.tick()
    v = rig.loop.state.version
    again = rig.make()
    rig.llm.responses["CopilotDelta"] = [CopilotDelta(notes_upsert=[NoteUpsert(text="second")])]
    await again.tick()  # restores; does not re-process old segments
    assert again.state.version == v and len(rig.llm.calls) == 1
    await rig.segs(3)
    await again.tick()
    assert [n.id for n in again.state.notes] == ["n1", "n2"]


async def title_of(sm: SM, sid: uuid.UUID) -> str | None:
    async with sm() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        return row.title


async def test_first_cycle_names_an_untitled_session(rig: Rig, sm: SM) -> None:
    rig.llm.responses["CopilotDelta"] = [
        CopilotDelta(title="  'Q3 vendor   renewal'  "),
        CopilotDelta(title="Something else entirely"),
    ]
    await rig.segs(3)
    await rig.loop.tick()
    assert "SESSION TITLE\n(untitled: propose one)" in rig.llm.calls[0].user
    assert await title_of(sm, rig.sid) == "Q3 vendor renewal"

    await rig.segs(3)
    await rig.loop.tick()  # named sessions keep their name; the prompt says so
    assert "SESSION TITLE\nQ3 vendor renewal" in rig.llm.calls[1].user
    assert await title_of(sm, rig.sid) == "Q3 vendor renewal"


async def test_auto_name_never_overwrites_a_title(rig: Rig, sm: SM) -> None:
    real = rig.llm.complete_json

    async def rename_then_answer(**kw: Any) -> Any:
        async with sm() as db:  # the user renames while the LLM call is in flight
            row = await db.get(SessionRow, rig.sid)
            assert row is not None
            row.title = "My name"
            await db.commit()
        return await real(**kw)

    rig.llm.complete_json = rename_then_answer  # type: ignore[method-assign]
    rig.llm.responses["CopilotDelta"] = [CopilotDelta(title="Copilot name")]
    await rig.segs(3)
    await rig.loop.tick()
    assert "(untitled: propose one)" in rig.llm.calls[0].user
    assert await title_of(sm, rig.sid) == "My name"


def test_delta_title_is_tidied() -> None:
    assert CopilotDelta(title="   ").title is None
    assert CopilotDelta(title='"Budget review"').title == "Budget review"
    long = CopilotDelta(title="word " * 40).title
    assert long is not None and len(long) <= 80 and not long.endswith(" ")


def test_status_model_still_valid() -> None:
    assert Status(stt_tier=None, llm_ok=True, lag_ms=0).llm_ok
