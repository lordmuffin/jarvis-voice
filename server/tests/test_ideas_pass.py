"""The ideas pass against a real Postgres: rows, audit events, the cursor, the loop's cadence,
and the end-of-session pass that puts work items in the note."""

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.schema import FinalNote
from jarvis_live.db.models import OWNER_USER_ID, CopilotItem, WorkItem, WorkItemEvent
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.llm.client import LLMError
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.users import create_user
from jarvis_live.workitems.ideas import CURSOR, IdeasLoop, IdeasPass
from jarvis_live.workitems.schema import IdeasDelta, ItemUpsert
from tests.conftest import FakeSTT
from tests.fakes import FakeClock, FakeLLM, FakeNotifier, add_segments, make_session, new_device
from tests.ideas_fixtures import parse
from tests.test_copilot_loop import FakeVault

SM = async_sessionmaker  # type: ignore[type-arg]
REPOS = (
    "lordmuffin/jarvis-voice   Jarvis Live server and apps\n"
    "lordmuffin/np-home-homelab  homelab GitOps"
)
CFG = Settings(work_items_enabled=True, work_repos=REPOS, ideas_overlap_s=60)
DIARY = parse("diary_three_ideas.txt")

GOTIFY = ItemUpsert(
    title="Retry failed Gotify notifications",
    repo="lordmuffin/jarvis-voice",
    intent="Retry a failed push with backoff.",
    acceptance_criteria=["A failed push is retried with backoff"],
    source_lines=["L2", "L3"],
)
NFS = ItemUpsert(
    title="Alert on TrueNAS nfsd saturation",
    repo="lordmuffin/np-home-homelab",
    source_lines=["L5", "L6"],
)


async def owner_session(sm: SM, **kw: Any) -> uuid.UUID:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=OWNER_USER_ID)
    return await make_session(sm, device_id, mode="solo", **kw)


async def items(sm: SM, sid: uuid.UUID) -> list[WorkItem]:
    async with sm() as db:
        rows = await db.execute(
            select(WorkItem).where(WorkItem.session_id == sid).order_by(WorkItem.ref)
        )
        return list(rows.scalars().all())


async def events(sm: SM, item_id: uuid.UUID) -> list[str]:
    async with sm() as db:
        rows = await db.execute(
            select(WorkItemEvent.event)
            .where(WorkItemEvent.work_item_id == item_id)
            .order_by(WorkItemEvent.id)
        )
        return list(rows.scalars().all())


async def test_a_pass_creates_items_with_audit_events_and_a_cursor(sm: SM) -> None:
    sid = await owner_session(sm)
    await add_segments(sm, sid, DIARY[:9])
    llm = FakeLLM().queue("IdeasDelta", IdeasDelta(items_upsert=[GOTIFY, NFS]))

    assert await IdeasPass(CFG, sm, llm).run(sid) is True

    rows = await items(sm, sid)
    assert [(r.ref, r.title, r.repo, r.state) for r in rows] == [
        ("w1", "Retry failed Gotify notifications", "lordmuffin/jarvis-voice", "draft"),
        ("w2", "Alert on TrueNAS nfsd saturation", "lordmuffin/np-home-homelab", "draft"),
    ]
    assert [s["start_ms"] for s in rows[0].sources] == [21_000, 34_000]
    assert rows[0].user_id == OWNER_USER_ID
    assert await events(sm, rows[0].id) == ["proposed"]
    call = llm.calls[0]
    assert call.schema == "IdeasDelta" and call.model == CFG.copilot_model
    assert "- lordmuffin/jarvis-voice: Jarvis Live server and apps" in call.user
    assert "L2 [00:21] me: The Gotify pushes" in call.user
    async with sm() as db:
        cursor = (
            await db.execute(
                select(CopilotItem.payload).where(
                    CopilotItem.session_id == sid, CopilotItem.kind == CURSOR
                )
            )
        ).scalar_one()
    assert cursor == {"until_ms": DIARY[8][1]}


async def test_the_next_pass_reads_only_new_lines_plus_overlap_and_clusters(sm: SM) -> None:
    sid = await owner_session(sm)
    await add_segments(sm, sid, DIARY[:9])
    llm = FakeLLM().queue("IdeasDelta", IdeasDelta(items_upsert=[GOTIFY]))
    await IdeasPass(CFG, sm, llm).run(sid)

    await add_segments(sm, sid, DIARY[9:])
    revisit = GOTIFY.model_copy(update={"ref": "w1", "source_lines": ["L4", "L5"]})
    llm.responses["IdeasDelta"] = [IdeasDelta(items_upsert=[revisit])]
    assert await IdeasPass(CFG, sm, llm).run(sid) is True

    second = llm.calls[1].user
    transcript = second.split("NEW TRANSCRIPT\n", 1)[1].splitlines()
    # The cursor is where the 05:30 line ended (05:45); 60 s of overlap reaches back to the
    # 05:12 line, which ended at 05:27. Nothing older is sent again.
    assert transcript[0].startswith("L1 [05:12]")
    assert transcript[3].startswith("L4 [21:03]")
    assert '"ref": "w1"' in second.split("OPEN WORK ITEMS", 1)[1]
    (w1,) = await items(sm, sid)
    assert [s["start_ms"] for s in w1.sources] == [21_000, 34_000, 1_263_000, 1_280_000]
    assert w1.version == 2
    assert await events(sm, w1.id) == ["proposed", "llm_updated"]


async def test_nothing_new_means_no_llm_call(sm: SM) -> None:
    sid = await owner_session(sm)
    await add_segments(sm, sid, DIARY[:3])
    llm = FakeLLM().queue("IdeasDelta", IdeasDelta())
    await IdeasPass(CFG, sm, llm).run(sid)
    assert await IdeasPass(CFG, sm, llm).run(sid) is False
    assert len(llm.calls) == 1


async def test_an_llm_failure_keeps_the_cursor_so_the_lines_are_seen_again(sm: SM) -> None:
    sid = await owner_session(sm)
    await add_segments(sm, sid, DIARY[:3])
    llm = FakeLLM().queue("IdeasDelta", LLMError("down"))
    assert await IdeasPass(CFG, sm, llm).run(sid) is False
    assert await items(sm, sid) == []
    llm.responses["IdeasDelta"] = [IdeasDelta(items_upsert=[GOTIFY])]
    assert await IdeasPass(CFG, sm, llm).run(sid) is True
    assert "L2 [00:21]" in llm.calls[1].user


async def test_an_owner_edit_made_while_the_llm_ran_is_kept(sm: SM) -> None:
    sid = await owner_session(sm)
    await add_segments(sm, sid, DIARY[:3])
    llm = FakeLLM().queue("IdeasDelta", IdeasDelta(items_upsert=[GOTIFY]))
    await IdeasPass(CFG, sm, llm).run(sid)
    (w1,) = await items(sm, sid)

    async with sm() as db:  # the owner renames it (the API locks the field)
        await db.execute(
            update(WorkItem)
            .where(WorkItem.id == w1.id)
            .values(title="Mine", locked_fields=["title"])
        )
        await db.commit()
    await add_segments(sm, sid, DIARY[3:5])
    llm.responses["IdeasDelta"] = [
        IdeasDelta(items_upsert=[GOTIFY.model_copy(update={"ref": "w1", "title": "LLM"})])
    ]
    await IdeasPass(CFG, sm, llm).run(sid)
    (w1,) = await items(sm, sid)
    assert w1.title == "Mine"


async def test_the_loop_runs_after_enough_segments_or_after_the_interval(sm: SM) -> None:
    sid = await owner_session(sm)
    clock = FakeClock()
    calls: list[uuid.UUID] = []

    class Spy(IdeasPass):
        async def run(self, session_id: uuid.UUID) -> bool:
            calls.append(session_id)
            return False

    cfg = Settings(work_items_enabled=True, ideas_min_new_segments=3, ideas_interval_s=120)
    loop = IdeasLoop(
        sid, settings=cfg, sessionmaker=sm, runner=Spy(cfg, sm, FakeLLM()), clock=clock
    )
    await add_segments(sm, sid, DIARY[:2])
    await loop.tick()
    assert calls == []  # 2 < 3 and the interval has not passed
    await add_segments(sm, sid, DIARY[2:3])
    await loop.tick()
    assert calls == [sid]  # 3 new
    await add_segments(sm, sid, DIARY[3:4])
    await loop.tick()
    await clock.advance(119)
    await loop.tick()
    assert len(calls) == 1
    await clock.advance(1)
    await loop.tick()
    assert len(calls) == 2  # one new segment, 120 s later


def orchestrator(sm: SM, llm: FakeLLM, tmp_path: Path, **kw: Any) -> Orchestrator:
    cfg = Settings(
        work_items_enabled=True,
        work_repos=REPOS,
        outbox_dir=tmp_path,
        public_base_url="https://live.example",
        **kw,
    )
    return Orchestrator(
        cfg, sm, Bus(), FakeSTT(), llm, FakeVault(), FakeNotifier(), clock=FakeClock()
    )


async def test_session_end_runs_a_last_pass_and_the_note_lists_the_items(
    sm: SM, tmp_path: Path
) -> None:
    sid = await owner_session(sm, status="finalizing")
    await add_segments(sm, sid, DIARY)
    llm = (
        FakeLLM()
        .queue("IdeasDelta", IdeasDelta(items_upsert=[GOTIFY, NFS]))
        .queue("FinalNote", FinalNote(title="Friday brain dump", summary="Ideas."))
    )
    orch = orchestrator(sm, llm, tmp_path)
    await orch._end(sid)

    assert [c.schema for c in llm.calls] == ["IdeasDelta", "FinalNote"]  # ideas first
    (note,) = list(tmp_path.glob("*.md"))
    text = note.read_text()
    section = text.split("## Work items\n", 1)[1].split("\n## ", 1)[0]
    assert "### Retry failed Gotify notifications" in section
    assert "`lordmuffin/jarvis-voice` · draft · [review](https://live.example/sessions/" in section
    assert f"[00:21](https://live.example/sessions/{sid}#t=21000)" in section
    assert "### Alert on TrueNAS nfsd saturation" in section
    assert "Claude Code prompt" not in section  # drafts carry no frozen prompt


async def test_other_users_and_local_only_sessions_are_not_mined(sm: SM, tmp_path: Path) -> None:
    plain = await create_user(sm, f"plain {uuid.uuid4().hex[:6]}")
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=plain.id)
    theirs = await make_session(sm, device_id, status="finalizing")
    await add_segments(sm, theirs, DIARY[:3])
    llm = FakeLLM().queue("FinalNote", FinalNote(title="t", summary="s"))
    orch = orchestrator(sm, llm, tmp_path)
    await orch._end(theirs)
    assert [c.schema for c in llm.calls] == ["FinalNote"]

    admin = await create_user(sm, f"admin {uuid.uuid4().hex[:6]}", is_admin=True)
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=admin.id)
    admins = await make_session(sm, device_id, status="finalizing")
    await add_segments(sm, admins, DIARY[:3])
    llm.queue("IdeasDelta", IdeasDelta())
    await orch._end(admins)
    assert "IdeasDelta" in [c.schema for c in llm.calls]

    local = await owner_session(sm, status="finalizing", local_only=True)
    await add_segments(sm, local, DIARY[:3])
    before = len(llm.calls)
    await orch._end(local)
    assert len(llm.calls) == before


async def test_work_items_off_means_no_pass(sm: SM, tmp_path: Path) -> None:
    sid = await owner_session(sm, status="finalizing")
    await add_segments(sm, sid, DIARY[:3])
    llm = FakeLLM().queue("FinalNote", FinalNote(title="t", summary="s"))
    cfg = Settings(outbox_dir=tmp_path)
    orch = Orchestrator(cfg, sm, Bus(), FakeSTT(), llm, FakeVault(), FakeNotifier())
    await orch._end(sid)
    assert [c.schema for c in llm.calls] == ["FinalNote"]
    async with sm() as db:
        assert (await db.get(SessionRow, sid)).status == "done"  # type: ignore[union-attr]
