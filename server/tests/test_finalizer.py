import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.auth import create_device
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.prompts import FINAL_SYSTEM, MAP_SYSTEM, REDUCE_SYSTEM
from jarvis_live.copilot.schema import FinalAction, FinalNote
from jarvis_live.db.models import CopilotItem
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.finalizer import Finalizer, split_windows, write_atomic
from jarvis_live.llm.client import LLMError
from jarvis_live.protocol import FinalNote as FinalNoteMsg
from tests.fakes import FakeLLM, FakeNotifier, add_segments, make_session
from tests.test_copilot_loop import FakeVault

SM = async_sessionmaker  # type: ignore[type-arg]

NOTE = FinalNote(
    title="Vendor call",
    summary="Talked to the vendor.",
    decisions=["Go ahead"],
    actions=[FinalAction(text="Send quote", owner="Dana")],
    related=["Acme Renewal", "Made Up Note"],
)


class Vault(FakeVault):
    async def resolve_name(self, name: str) -> str | None:
        return "Acme Renewal" if name.lower() == "acme renewal" else None


class Rig:
    def __init__(self, sm: Any, tmp_path: Path, llm: FakeLLM, **cfg: Any) -> None:
        self.sm = sm
        self.llm = llm
        self.notifier = FakeNotifier()
        self.bus = Bus()
        self.settings = Settings(
            outbox_dir=tmp_path / "outbox",
            public_base_url="https://jarvis.example/",
            timezone="America/Chicago",
            **cfg,
        )
        self.fin = Finalizer(self.settings, sm, self.bus, llm, Vault(), self.notifier)

    async def session(self, **kw: Any) -> uuid.UUID:
        device_id, _ = await create_device(self.sm, f"Andrew's Mac {uuid.uuid4().hex[:6]}")
        kw.setdefault("status", "finalizing")
        kw.setdefault("ended_at", datetime(2026, 10, 9, 15, 12, tzinfo=UTC))
        return await make_session(self.sm, device_id, **kw)

    async def status(self, sid: uuid.UUID) -> str:
        async with self.sm() as db:
            row = await db.get(SessionRow, sid)
            assert row is not None
            return row.status

    def files(self) -> list[Path]:
        return sorted(p for p in self.settings.outbox_dir.glob("*") if p.is_file())  # type: ignore[union-attr]


@pytest.fixture
def mk(sm: SM, tmp_path: Path) -> Any:
    def make(llm: FakeLLM | None = None, **cfg: Any) -> Rig:
        return Rig(sm, tmp_path, llm or FakeLLM().queue("FinalNote", NOTE), **cfg)

    return make


async def test_writes_note_sets_done_publishes_and_notifies(mk: Any) -> None:
    rig: Rig = mk()
    sid = await rig.session()
    await add_segments(
        rig.sm, sid, [(1000, 4000, "them", "Hello."), (5000, 8000, "me", "Send the quote.")]
    )
    sub = rig.bus.subscribe(str(sid))
    async with rig.sm() as db:
        db.add(
            CopilotItem(
                session_id=sid,
                kind="related",
                payload={"hits": [{"path": "Areas/Garden.md"}]},
            )
        )
        await db.commit()

    result = await rig.fin.run(sid)

    assert result is not None and result.title == "Vendor call"
    (f,) = rig.files()
    assert f.name == "2026-10-09 0930 - Vendor call.md"  # 14:30Z in America/Chicago
    text = f.read_text()
    assert f"session_id: {sid}" in text and "type: live-session" in text
    assert "duration_minutes: 42" in text
    assert "- [ ] Send quote (owner: Dana)" in text
    assert "[00:00:05] **Me:** Send the quote." in text
    # wikilinks: what the copilot showed + resolvable model suggestions; invented names dropped
    assert "- [[Garden]]" in text and "- [[Acme Renewal]]" in text
    assert "Made Up Note" not in text
    assert not [p for p in rig.settings.outbox_dir.iterdir() if p.name.endswith(".tmp")]  # type: ignore[union-attr]
    assert await rig.status(sid) == "done"

    ev = await sub.get()
    assert isinstance(ev, FinalNoteMsg) and ev.path == f.name and ev.title == "Vendor call"
    (sent,) = rig.notifier.sent
    assert sent.priority == 5 and sent.url == f"https://jarvis.example/sessions/{sid}"
    (call,) = rig.llm.calls
    assert call.model == "heavy" and call.system == FINAL_SYSTEM
    assert not call.system.startswith("/no_think")
    assert "[00:05] me: Send the quote." in call.user

    async with rig.sm() as db:
        kinds = (
            (await db.execute(select(CopilotItem.kind).where(CopilotItem.session_id == sid)))
            .scalars()
            .all()
        )
    assert "final_note" in kinds
    assert await rig.fin.run(sid) is None  # already done: idempotent


async def test_untitled_session_takes_the_note_title(mk: Any) -> None:
    rig: Rig = mk()
    untitled, titled = await rig.session(), await rig.session()
    async with rig.sm() as db:
        row = await db.get(SessionRow, titled)
        assert row is not None
        row.title = "Named by me"
        await db.commit()
    for sid in (untitled, titled):
        await add_segments(rig.sm, sid, [(1000, 4000, "them", "Hello.")])
        await rig.fin.run(sid)
    async with rig.sm() as db:
        assert (await db.get(SessionRow, untitled)).title == "Vendor call"  # type: ignore[union-attr]
        assert (await db.get(SessionRow, titled)).title == "Named by me"  # type: ignore[union-attr]


async def test_map_reduce_over_20_minute_windows(mk: Any) -> None:
    min_ = 60_000
    partial = lambda t: FinalNote(title=t, summary=t)  # noqa: E731
    llm = FakeLLM().queue(
        "FinalNote", partial("part one"), partial("part two"), partial("part three"), NOTE
    )
    rig: Rig = mk(llm, final_max_input_tokens=100)
    sid = await rig.session()
    await add_segments(
        rig.sm,
        sid,
        [
            (0, 5000, "me", "early " * 40),
            (21 * min_, 21 * min_ + 5000, "them", "middle " * 40),
            (45 * min_, 45 * min_ + 5000, "me", "late " * 40),
        ],
    )
    result = await rig.fin.run(sid)

    assert result is not None and result.title == "Vendor call"
    assert [c.system for c in llm.calls] == [MAP_SYSTEM] * 3 + [REDUCE_SYSTEM]
    assert "early" in llm.calls[0].user and "middle" not in llm.calls[0].user
    assert "[21:00] them: middle" in llm.calls[1].user  # absolute timestamps in every window
    assert "[45:00] me: late" in llm.calls[2].user
    for t in ("part one", "part two", "part three"):
        assert t in llm.calls[3].user
    assert "early" in rig.files()[0].read_text()  # full transcript still in the note


async def test_single_pass_when_under_budget(mk: Any) -> None:
    rig: Rig = mk()
    sid = await rig.session()
    await add_segments(rig.sm, sid, [(0, 1000, "me", "short")])
    await rig.fin.run(sid)
    assert len(rig.llm.calls) == 1


def test_split_windows_by_start_time() -> None:
    from jarvis_live.copilot.transcript import Line

    lines = [Line(s * 60_000, s * 60_000 + 1, "me", str(s)) for s in (0, 19, 20, 41)]
    assert [[x.text for x in w] for w in split_windows(lines, 1_200_000)] == [
        ["0", "19"],
        ["20"],
        ["41"],
    ]


async def test_failure_sets_failed_and_notifies(mk: Any) -> None:
    rig: Rig = mk(FakeLLM().queue("FinalNote", LLMError("boom")))
    sid = await rig.session()
    await add_segments(rig.sm, sid, [(0, 1000, "me", "hi")])
    assert await rig.fin.run(sid) is None
    assert await rig.status(sid) == "failed"
    assert rig.files() == []
    (sent,) = rig.notifier.sent
    assert sent.priority == 8 and "failed" in sent.title.lower()


async def test_local_only_never_reaches_llm(mk: Any) -> None:
    rig: Rig = mk()
    sid = await rig.session(local_only=True)
    await add_segments(rig.sm, sid, [(0, 1000, "me", "private")])
    assert await rig.fin.run(sid) is None
    assert rig.llm.calls == [] and rig.files() == [] and rig.notifier.sent == []
    assert await rig.status(sid) == "done"


async def test_empty_transcript_is_done_without_a_note(mk: Any) -> None:
    rig: Rig = mk()
    sid = await rig.session()
    assert await rig.fin.run(sid) is None
    assert rig.llm.calls == [] and rig.files() == []
    assert await rig.status(sid) == "done"


async def test_draft_fallback_in_final_transcript(mk: Any) -> None:
    rig: Rig = mk()
    sid = await rig.session()
    await add_segments(rig.sm, sid, [(0, 1000, "me", "only a draft")], source="draft")
    await rig.fin.run(sid)
    assert "only a draft" in rig.files()[0].read_text()


async def test_cancellation_leaves_session_finalizing(mk: Any) -> None:
    llm = FakeLLM().queue("FinalNote", NOTE)
    llm.delay_s = 5
    rig: Rig = mk(llm)
    sid = await rig.session()
    await add_segments(rig.sm, sid, [(0, 1000, "me", "hi")])
    task = asyncio.create_task(rig.fin.run(sid))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await rig.status(sid) == "finalizing" and rig.notifier.sent == []


def test_write_atomic_collisions_and_idempotence(tmp_path: Path) -> None:
    out = tmp_path / "outbox"
    assert write_atomic(out, "n.md", "session_id: A\none", "A") == "n.md"
    assert write_atomic(out, "n.md", "session_id: B\ntwo", "B") == "n (2).md"
    assert write_atomic(out, "n.md", "session_id: A\nthree", "A") == "n.md"  # same session
    assert (out / "n.md").read_text().endswith("three")
    assert sorted(p.name for p in out.iterdir()) == ["n (2).md", "n.md"]  # no tmp left behind
