"""How proposals get from the LLM to the review queue: lenient parsing, grounding in the real
transcript, one shared state per session, the copilot loop, and the finalizer's note."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from jarvis_live.actions.intake import ActionIntake
from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.service import ActionService
from jarvis_live.actions.state import Context, ground
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.loop import CopilotLoop
from jarvis_live.copilot.prompts import COPILOT_SYSTEM, FINAL_SYSTEM
from jarvis_live.copilot.schema import CopilotDelta, FinalNote
from jarvis_live.copilot.transcript import Line
from jarvis_live.db.models import OWNER_USER_ID, ActionProposal
from jarvis_live.finalize.finalizer import Finalizer
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.users import create_user
from tests.fakes import FakeClock, FakeLLM, FakeNotifier, add_segments, make_session, new_device
from tests.test_copilot_loop import FakeVault
from tests.work_item_helpers import SM, owner_with_session

LINES = [
    Line(60_000, 64_000, "me", "Add a task to call the vendor about the quote."),
    Line(70_000, 72_000, "them", "Sure."),
]


def ctx() -> Context:
    return Context(datetime.now(UTC), "UTC", lambda p: True, frozenset())


def intake(sm: SM, **cfg: Any) -> ActionIntake:
    svc = ActionService(Settings(actions_enabled=True, **cfg), sm, {}, ctx)
    return ActionIntake(svc, ctx)


def task(text: str = "Call the vendor", at: str = "01:01") -> dict[str, Any]:
    return {"kind": "task.create", "text": text, "evidence": [{"at": at, "text": "x"}]}


# --- parsing and grounding -----------------------------------------------------------------


def test_a_malformed_proposal_is_dropped_without_failing_the_delta() -> None:
    d = CopilotDelta.model_validate(
        {"proposals": [task(), {"kind": "delete.everything"}, "nonsense", {"kind": "task.create"}]}
    )
    assert [p.kind for p in d.proposals] == ["task.create", "task.create"]
    assert d.proposals[0].evidence == [Evidence(start_ms=61_000, text="x")]  # "01:01" -> ms


def test_evidence_is_replaced_by_the_real_transcript_line() -> None:
    p = ProposalIn.model_validate(task(at="01:02"))  # a second after the line started
    [g] = ground([p], LINES)
    assert g.evidence == [Evidence(start_ms=60_000, text=LINES[0].text)]


def test_evidence_outside_the_transcript_drops_the_proposal() -> None:
    assert ground([ProposalIn.model_validate(task(at="09:00"))], LINES) == []


def test_prompts_ask_for_explicit_intent_only() -> None:
    for system in (COPILOT_SYSTEM, FINAL_SYSTEM):
        assert "proposals" in system and "explicitly" in system


# --- intake --------------------------------------------------------------------------------


async def test_intake_stores_grounded_proposals_with_who_proposed(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    sp = intake(sm).for_session(sid, OWNER_USER_ID)
    n = await sp.propose([ProposalIn.model_validate(task())], LINES, proposed_by="copilot:fast")
    assert n == 1
    async with sm() as db:
        [row] = (
            await db.scalars(select(ActionProposal).where(ActionProposal.session_id == sid))
        ).all()
    assert (row.ref, row.state, row.proposed_by) == ("p1", "proposed", "copilot:fast")
    assert row.evidence == [{"start_ms": 60_000, "text": LINES[0].text}]


async def test_live_and_finalizer_share_dedupe_and_refs_survive_a_restart(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    a = intake(sm).for_session(sid, OWNER_USER_ID)
    await a.propose([ProposalIn.model_validate(task("One"))], LINES, proposed_by="copilot:m")
    b = intake(sm).for_session(sid, OWNER_USER_ID)  # a new process: state comes from the database
    again = [ProposalIn.model_validate(task("one")), ProposalIn.model_validate(task("Two"))]
    assert await b.propose(again, LINES, proposed_by="finalizer:m") == 1
    assert [(p.ref, p.payload["text"]) for p in await b.listing()] == [("p1", "One"), ("p2", "Two")]


async def test_a_rejected_proposal_is_not_proposed_again_and_the_model_is_told(sm: SM) -> None:
    from jarvis_live.workitems.dispatch import Actor

    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    it = intake(sm)
    sp = it.for_session(sid, OWNER_USER_ID)
    await sp.propose([ProposalIn.model_validate(task("One"))], LINES, proposed_by="copilot:m")
    [p] = await sp.listing()
    await it.service.reject(p.id, Actor(OWNER_USER_ID, "web"))
    fresh = intake(sm).for_session(sid, OWNER_USER_ID)
    assert (
        await fresh.propose([ProposalIn.model_validate(task("One"))], LINES, proposed_by="m") == 0
    )
    assert await fresh.known() == ["task.create: One"]


# --- the live copilot ----------------------------------------------------------------------


async def test_the_copilot_loop_files_proposals_and_never_executes(sm: SM) -> None:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}")
    sid = await make_session(sm, device_id)
    delta = CopilotDelta.model_validate({"proposals": [task(at="00:01")]})
    llm = FakeLLM().queue("CopilotDelta", delta)
    it = intake(sm)
    loop = CopilotLoop(
        sid,
        settings=Settings(),
        sessionmaker=sm,
        bus=Bus(),
        llm=llm,
        vault=FakeVault(),
        stream_ms=lambda: 5000,
        clock=FakeClock(),
        proposals=it.for_session(sid, OWNER_USER_ID),
    )
    await loop.tick()  # restore (nothing yet)
    await add_segments(sm, sid, [(1000, 1900, "me", "Add a task to call the vendor.")] * 3)
    await loop.tick()
    async with sm() as db:
        [row] = (
            await db.scalars(select(ActionProposal).where(ActionProposal.session_id == sid))
        ).all()
    assert (row.state, row.proposed_by) == ("proposed", f"copilot:{Settings().copilot_model}")
    assert row.evidence[0]["text"] == "Add a task to call the vendor."
    assert "proposals_already_made" not in llm.calls[0].user  # none yet at the first cycle


# --- the finalizer -------------------------------------------------------------------------


def fin(sm: SM, tmp_path: Path, note: FinalNote, with_intake: bool = True) -> tuple[Finalizer, Any]:
    cfg = Settings(
        outbox_dir=tmp_path / "outbox",
        public_base_url="https://jarvis.example",
        actions_enabled=True,
    )
    notifier = FakeNotifier()
    f = Finalizer(
        cfg,
        sm,
        Bus(),
        FakeLLM().queue("FinalNote", note),
        FakeVault(),
        notifier,
        intake=ActionIntake(ActionService(cfg, sm, {}, ctx), ctx) if with_intake else None,
    )
    return f, notifier


async def finalizing(sm: SM, user_id: uuid.UUID = OWNER_USER_ID) -> uuid.UUID:
    device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=user_id)
    sid = await make_session(sm, device_id, status="finalizing")
    await add_segments(sm, sid, [(60_000, 64_000, "me", "Remind me to send the invoice Monday.")])
    return sid


def note_with(*proposals: dict[str, Any]) -> FinalNote:
    return FinalNote.model_validate(
        {"title": "Invoice", "summary": "s", "proposals": list(proposals)}
    )


async def test_the_note_lists_proposed_actions_before_the_transcript(
    sm: SM, tmp_path: Path
) -> None:
    sid = await finalizing(sm)
    f, notifier = fin(sm, tmp_path, note_with(task("Send the invoice", at="01:01")))
    result = await f.run(sid)
    assert result is not None
    text = (tmp_path / "outbox" / result.path).read_text()
    section = text.split("## Proposed actions\n", 1)[1].split("\n## ", 1)[0]
    assert "- [ ] Task: Send the invoice <!-- action:p1 -->" in section
    assert f"(https://jarvis.example/sessions/{sid})" in section
    assert text.index("## Proposed actions") < text.index("## Transcript")
    assert "1 action waiting" in notifier.sent[0].message
    async with sm() as db:
        [row] = (
            await db.scalars(select(ActionProposal).where(ActionProposal.session_id == sid))
        ).all()
    assert (row.state, row.proposed_by) == ("proposed", "finalizer:heavy")


async def test_no_section_for_other_users_or_when_intake_is_off(sm: SM, tmp_path: Path) -> None:
    other = await create_user(sm, f"other {uuid.uuid4().hex[:6]}")  # neither owner nor admin
    sids = []
    for user_id, with_intake in ((other.id, True), (OWNER_USER_ID, False)):
        sid = await finalizing(sm, user_id)
        sids.append(sid)
        f, _ = fin(sm, tmp_path, note_with(task(at="01:01")), with_intake)
        result = await f.run(sid)
        assert result is not None
        assert "Proposed actions" not in (tmp_path / "outbox" / result.path).read_text()
    async with sm() as db:
        found = await db.scalars(select(ActionProposal).where(ActionProposal.session_id.in_(sids)))
        assert found.all() == []


# --- who gets proposals --------------------------------------------------------------------


async def test_only_reviewable_sessions_get_a_proposal_door(sm: SM, tmp_path: Path) -> None:
    from jarvis_live.db.models import Session as SessionRow
    from tests.conftest import FakeSTT

    async def row_of(user_id: uuid.UUID, **kw: Any) -> SessionRow:
        device_id, _ = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=user_id)
        sid = await make_session(sm, device_id, **kw)
        async with sm() as db:
            r = await db.get(SessionRow, sid)
            assert r is not None
            return r

    def orch(enabled: bool, with_intake: bool = True) -> Orchestrator:
        cfg = Settings(outbox_dir=tmp_path, actions_enabled=enabled)
        return Orchestrator(
            cfg,
            sm,
            Bus(),
            FakeSTT(),
            FakeLLM(),
            FakeVault(),
            FakeNotifier(),
            intake=intake(sm) if with_intake else None,
        )

    other = await create_user(sm, f"other {uuid.uuid4().hex[:6]}")
    mine, theirs = await row_of(OWNER_USER_ID), await row_of(other.id)
    local = await row_of(OWNER_USER_ID, local_only=True)
    on = orch(True)
    assert await on._proposals_for(mine) is not None
    assert await on._proposals_for(theirs) is None  # not the vault's owner or an admin
    assert await on._proposals_for(local) is None  # never leaves the device's control
    assert await orch(False)._proposals_for(mine) is None  # feature off
    assert await orch(True, with_intake=False)._proposals_for(mine) is None
