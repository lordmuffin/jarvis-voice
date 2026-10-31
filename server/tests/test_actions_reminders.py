"""Scheduled Gotify reminders: approving one stores it, the loop sends it once, and a delivery
that fails is reported, never retried silently."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from jarvis_live.actions.reminders import ReminderExecutor, ReminderLoop
from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.service import ActionService
from jarvis_live.actions.state import Context, ProposalState
from jarvis_live.config import Settings
from jarvis_live.db.models import (
    OWNER_USER_ID,
    ActionProposal,
    ActionProposalEvent,
    ScheduledReminder,
)
from jarvis_live.users import create_user
from jarvis_live.workitems.dispatch import Actor
from tests.fakes import FakeNotifier
from tests.work_item_helpers import SM, owner_with_session


@pytest.fixture(autouse=True)
async def _no_leftover_reminders(sm: SM) -> None:
    """The database outlives a test, and ``tick`` fires everything that is due."""
    async with sm() as db:
        await db.execute(delete(ScheduledReminder))
        await db.commit()


EV = [Evidence(start_ms=5000, text="remind me")]


def ctx() -> Context:
    return Context(datetime.now(UTC), "UTC", lambda p: True, frozenset())


class Rig:
    def __init__(self, sm: SM, notifier: FakeNotifier | None = None) -> None:
        self.sm = sm
        self.notifier = notifier or FakeNotifier()
        self.cfg = Settings(actions_enabled=True, public_base_url="https://live.example")
        self.executor = ReminderExecutor(self.cfg, sm, self.notifier)
        self.loop = ReminderLoop(self.cfg, sm, self.notifier)
        self.svc = ActionService(self.cfg, sm, {"reminder.schedule": self.executor}, ctx)

    async def approved(
        self, user_id: uuid.UUID = OWNER_USER_ID, *, hours: float = 2, body: str | None = "Monday"
    ) -> tuple[uuid.UUID, uuid.UUID, datetime]:
        """A reminder proposed, approved and executed (so scheduled): ``(session, id, fire_at)``."""
        sid, _ = await owner_with_session(sm=self.sm, user_id=user_id)
        fire = datetime.now(UTC) + timedelta(hours=hours)
        [p] = ProposalState().apply(
            [
                ProposalIn(
                    kind="reminder.schedule",
                    title="Send the invoice",
                    body=body,
                    at=fire.isoformat(),
                    evidence=EV,
                )
            ],
            ctx(),
        )
        [pid] = await self.svc.admit(sid, user_id, [p], proposed_by="copilot:m")
        actor = Actor(user_id, "web")
        await self.svc.approve(sid, actor, [(pid, p.payload_sha256)])
        await self.svc.execute_approved(sid)
        return sid, pid, datetime.fromisoformat(p.payload["at"])

    async def proposal(self, pid: uuid.UUID) -> ActionProposal:
        async with self.sm() as db:
            row = await db.get(ActionProposal, pid, populate_existing=True)
            assert row is not None
            return row

    async def reminder(self, pid: uuid.UUID) -> ScheduledReminder:
        async with self.sm() as db:
            return (
                await db.scalars(
                    select(ScheduledReminder).where(ScheduledReminder.proposal_id == pid)
                )
            ).one()

    async def events(self, pid: uuid.UUID) -> list[str]:
        async with self.sm() as db:
            rows = await db.scalars(
                select(ActionProposalEvent.event)
                .where(ActionProposalEvent.proposal_id == pid)
                .order_by(ActionProposalEvent.id)
            )
            return list(rows.all())


async def test_approving_schedules_it_and_nothing_is_sent_before_it_is_due(sm: SM) -> None:
    rig = Rig(sm)
    _, pid, fire = await rig.approved()
    row = await rig.proposal(pid)
    assert row.state == "succeeded" and row.result is not None
    assert row.result["fire_at"] == fire.isoformat() and row.result["reminder_state"] == "pending"
    await rig.loop.tick(now=fire - timedelta(minutes=1))
    assert rig.notifier.sent == []


async def test_it_is_sent_once_when_due(sm: SM) -> None:
    rig = Rig(sm)
    sid, pid, fire = await rig.approved()
    await rig.loop.tick(now=fire + timedelta(seconds=5))
    await rig.loop.tick(now=fire + timedelta(seconds=40))
    [sent] = rig.notifier.sent
    assert "Send the invoice" in sent.title and "Monday" in sent.message
    assert sent.url == f"https://live.example/sessions/{sid}#t=5000"
    assert (await rig.reminder(pid)).state == "sent"
    row = await rig.proposal(pid)
    assert row.state == "succeeded" and (row.result or {})["reminder_state"] == "sent"
    assert (await rig.events(pid))[-1] == "reminder_sent"


async def test_a_late_reminder_says_so(sm: SM) -> None:
    rig = Rig(sm)
    _, _, fire = await rig.approved()
    await rig.loop.tick(now=fire + timedelta(hours=3))
    assert "Was due" in rig.notifier.sent[0].message


async def test_concurrent_ticks_send_it_once(sm: SM) -> None:
    rig = Rig(sm)
    _, _, fire = await rig.approved()
    await asyncio.gather(*(rig.loop.tick(now=fire + timedelta(seconds=1)) for _ in range(3)))
    assert len(rig.notifier.sent) == 1


async def test_a_failed_delivery_is_reported_and_not_retried(sm: SM) -> None:
    rig = Rig(sm)
    _, pid, fire = await rig.approved()
    rig.notifier.fail = "Gotify answered 502"
    await rig.loop.tick(now=fire + timedelta(seconds=1))
    rig.notifier.fail = None  # Gotify is back; nothing may quietly try again
    await rig.loop.tick(now=fire + timedelta(minutes=5))
    assert rig.notifier.sent == []
    r = await rig.reminder(pid)
    assert (r.state, r.error) == ("failed", "Gotify answered 502")
    result = (await rig.proposal(pid)).result or {}
    assert result["reminder_state"] == "failed" and "502" in result["reminder_error"]
    assert (await rig.events(pid))[-1] == "reminder_failed"


async def test_a_restart_mid_send_is_reported_not_resent(sm: SM) -> None:
    rig = Rig(sm)
    _, pid, fire = await rig.approved()
    async with sm() as db:  # the process died after claiming it, before hearing back
        await db.execute(
            update(ScheduledReminder)
            .where(ScheduledReminder.proposal_id == pid)
            .values(state="sending")
        )
        await db.commit()
    await rig.loop.recover()
    await rig.loop.tick(now=fire + timedelta(hours=1))
    assert rig.notifier.sent == []
    r = await rig.reminder(pid)
    assert r.state == "failed" and "interrupted" in (r.error or "")


async def test_scheduling_twice_stores_one_reminder(sm: SM) -> None:
    rig = Rig(sm)
    _, pid, _ = await rig.approved()
    row = await rig.proposal(pid)
    again = await rig.executor.execute(row)
    assert again["reminder_state"] == "pending"
    async with sm() as db:
        found = await db.scalars(
            select(ScheduledReminder).where(ScheduledReminder.proposal_id == pid)
        )
        assert len(found.all()) == 1
    assert (await rig.executor.reconcile(row)) is not None


async def test_only_the_owners_reminders_reach_the_owners_phone(sm: SM) -> None:
    rig = Rig(sm)
    admin = await create_user(sm, f"admin {uuid.uuid4().hex[:6]}")
    _, pid, fire = await rig.approved(admin.id)
    row = await rig.proposal(pid)
    assert row.state == "failed" and "owner" in (row.result or {})["error"]
    await rig.loop.tick(now=fire + timedelta(hours=1))
    assert rig.notifier.sent == []


async def test_without_gotify_approval_fails_visibly(sm: SM) -> None:
    notifier = FakeNotifier()
    notifier.configured = False
    rig = Rig(sm, notifier)
    _, pid, _ = await rig.approved()
    row = await rig.proposal(pid)
    assert row.state == "failed" and "not configured" in (row.result or {})["error"]
