"""Reviewing and executing action proposals: a real Postgres, fake executors."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.service import ActionError, ActionService, ExecutionError
from jarvis_live.actions.state import Context, Proposal, ProposalState
from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, ActionProposal, ActionProposalEvent
from jarvis_live.users import create_user
from jarvis_live.workitems.dispatch import Actor
from tests.work_item_helpers import SM, owner_with_session

EV = [Evidence(start_ms=1000, text="add a task")]
WEB = Actor(OWNER_USER_ID, "web")
DEVICE = Actor(OWNER_USER_ID, "device")


class FakeExecutor:
    def __init__(self, fail: set[str] | None = None, found: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail or set()
        self.found = found

    async def execute(self, proposal: ActionProposal) -> dict[str, Any]:
        await asyncio.sleep(0.01)
        self.calls.append(proposal.payload["text"])
        if proposal.payload["text"] in self.fail:
            raise ExecutionError("tracker is unreachable")
        return {"commit": "abc123"}

    async def reconcile(self, proposal: ActionProposal) -> dict[str, Any] | None:
        return {"commit": "already-there"} if self.found else None


def ctx() -> Context:
    return Context(
        now=datetime.now(UTC),
        timezone="UTC",
        vault_has=lambda p: True,
        repos=frozenset({"lordmuffin/jarvis-voice"}),
    )


def make(sm: SM, ex: FakeExecutor | None = None, **kw: Any) -> tuple[ActionService, FakeExecutor]:
    ex = ex or FakeExecutor()
    svc = ActionService(Settings(actions_enabled=True, **kw), sm, {"task.create": ex}, ctx)
    return svc, ex


def tasks(*texts: str) -> list[Proposal]:
    return ProposalState().apply(
        [ProposalIn(kind="task.create", text=t, evidence=EV) for t in texts], ctx()
    )


async def row(sm: SM, pid: uuid.UUID) -> ActionProposal:
    async with sm() as db:
        r = await db.get(ActionProposal, pid)
        assert r is not None
        return r


async def audit(sm: SM, pid: uuid.UUID) -> list[tuple[str, str]]:
    async with sm() as db:
        rows = await db.execute(
            select(ActionProposalEvent.event, ActionProposalEvent.actor_kind)
            .where(ActionProposalEvent.proposal_id == pid)
            .order_by(ActionProposalEvent.id)
        )
        return [(e, k) for e, k in rows.all()]


async def test_admitting_a_proposal_executes_nothing(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm)
    [pid] = await svc.admit(sid, OWNER_USER_ID, tasks("Call the vendor"), proposed_by="copilot:m")
    await asyncio.sleep(0.05)
    await svc.execute_approved(sid)
    r = await row(sm, pid)
    assert (r.state, ex.calls) == ("proposed", [])
    assert await audit(sm, pid) == [("proposed", "system")]


async def test_admitting_the_same_proposal_twice_keeps_one(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, _ = make(sm)
    p = tasks("Call the vendor")
    assert len(await svc.admit(sid, OWNER_USER_ID, p, proposed_by="copilot:m")) == 1
    assert await svc.admit(sid, OWNER_USER_ID, p, proposed_by="copilot:m") == []
    assert len(await svc.for_session(sid, OWNER_USER_ID)) == 1


async def test_approve_then_execute_records_who_when_and_the_result(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    [out] = await svc.approve(sid, DEVICE, [(pid, p.payload_sha256)])
    assert out.outcome == "approved"
    await svc.execute_approved(sid)
    r = await row(sm, pid)
    assert (r.state, r.result, r.approved_by) == ("succeeded", {"commit": "abc123"}, OWNER_USER_ID)
    assert r.approved_at is not None and r.executed_at is not None
    assert await audit(sm, pid) == [
        ("proposed", "system"),
        ("approved", "device"),
        ("exec_started", "system"),
        ("exec_succeeded", "system"),
    ]
    assert ex.calls == ["Call the vendor"]


async def test_reapproving_never_executes_twice(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    await svc.approve(sid, WEB, [(pid, p.payload_sha256)])
    await svc.execute_approved(sid)
    [again] = await svc.approve(sid, WEB, [(pid, p.payload_sha256)])
    await svc.execute_approved(sid)
    assert again.outcome == "already"
    assert ex.calls == ["Call the vendor"]


async def test_concurrent_executions_run_the_action_once(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    await svc.approve(sid, WEB, [(pid, p.payload_sha256)])
    await asyncio.gather(svc.execute_approved(sid), svc.execute_approved(sid))
    assert ex.calls == ["Call the vendor"]


async def test_a_stale_hash_is_refused_and_nothing_runs(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    edited = await svc.edit(pid, WEB, {"text": "Call the other vendor"})
    assert edited.payload_sha256 != p.payload_sha256
    [out] = await svc.approve(sid, WEB, [(pid, p.payload_sha256)])  # what the client had seen
    await svc.execute_approved(sid)
    assert out.outcome == "stale"
    assert ((await row(sm, pid)).state, ex.calls) == ("proposed", [])
    assert [e for e, _ in await audit(sm, pid)] == ["proposed", "edited"]


async def test_a_batch_runs_in_order_and_reports_a_failure_without_stopping(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm, FakeExecutor(fail={"two"}))
    ps = tasks("one", "two", "three")
    ids = await svc.admit(sid, OWNER_USER_ID, ps, proposed_by="finalizer:m")
    await svc.approve(sid, WEB, [(i, p.payload_sha256) for i, p in zip(ids, ps, strict=True)])
    await svc.execute_approved(sid)
    assert ex.calls == ["one", "two", "three"]
    states = [(await row(sm, i)).state for i in ids]
    assert states == ["succeeded", "failed", "succeeded"]
    failed = await row(sm, ids[1])
    assert failed.result == {"error": "tracker is unreachable"}
    assert [e for e, _ in await audit(sm, ids[1])][-1] == "exec_failed"


async def test_a_failed_action_is_not_retried_until_the_owner_asks(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    ex = FakeExecutor(fail={"one"})
    svc, _ = make(sm, ex)
    [p] = tasks("one")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    await svc.approve(sid, WEB, [(pid, p.payload_sha256)])
    await svc.execute_approved(sid)
    await svc.execute_approved(sid)
    assert ex.calls == ["one"]  # no silent retry
    ex.fail.clear()
    await svc.retry(pid, WEB)
    await svc.execute_approved(sid)
    assert ex.calls == ["one", "one"]
    assert (await row(sm, pid)).state == "succeeded"
    assert "retry_requested" in [e for e, _ in await audit(sm, pid)]


async def test_a_kind_without_an_executor_fails_visibly(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, _ = make(sm)
    [p] = ProposalState().apply(
        [
            ProposalIn(
                kind="reminder.schedule",
                title="t",
                at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
                evidence=EV,
            )
        ],
        ctx(),
    )
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    await svc.approve(sid, WEB, [(pid, p.payload_sha256)])
    await svc.execute_approved(sid)
    r = await row(sm, pid)
    assert r.state == "failed" and "not available" in (r.result or {})["error"]


async def test_reject_closes_it_for_good(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    await svc.reject(pid, WEB)
    [out] = await svc.approve(sid, WEB, [(pid, p.payload_sha256)])
    await svc.execute_approved(sid)
    assert out.outcome == "not_open" and ex.calls == []
    assert (await row(sm, pid)).state == "rejected"


async def test_unreviewed_proposals_expire_and_never_run(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, ex = make(sm, actions_ttl_days=14)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    async with sm() as db:
        await db.execute(
            update(ActionProposal)
            .where(ActionProposal.id == pid)
            .values(created_at=datetime.now(UTC) - timedelta(days=15))
        )
        await db.commit()
    assert await svc.expire_stale() == 1
    await svc.execute_approved(sid)
    assert ((await row(sm, pid)).state, ex.calls) == ("expired", [])


@pytest.mark.parametrize(("found", "state"), [(True, "succeeded"), (False, "failed")])
async def test_recovery_only_reconciles_and_never_rewrites(sm: SM, found: bool, state: str) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    ex = FakeExecutor(found=found)
    svc, _ = make(sm, ex)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    await svc.approve(sid, WEB, [(pid, p.payload_sha256)])
    async with sm() as db:  # the process died mid-execution
        await db.execute(
            update(ActionProposal).where(ActionProposal.id == pid).values(state="executing")
        )
        await db.commit()
    await svc.recover()
    r = await row(sm, pid)
    assert r.state == state and ex.calls == []
    if not found:
        assert "interrupted" in (r.result or {})["error"]


async def test_other_users_cannot_see_or_act(sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    svc, _ = make(sm)
    [p] = tasks("Call the vendor")
    [pid] = await svc.admit(sid, OWNER_USER_ID, [p], proposed_by="copilot:m")
    other = await create_user(sm, f"other {uuid.uuid4().hex[:6]}")
    with pytest.raises(ActionError) as e:
        await svc.get(pid, other.id)
    assert e.value.status == 404
    with pytest.raises(ActionError):
        await svc.reject(pid, Actor(other.id, "web"))
    assert await svc.for_session(sid, other.id) == []
