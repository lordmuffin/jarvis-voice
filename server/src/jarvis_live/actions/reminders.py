"""Scheduled reminders: approving a ``reminder.schedule`` action stores a row; the loop pushes
it through Gotify when it is due.

A reminder fires at most once. The loop claims a row before it pushes, so a crash in between is
settled as failed ("interrupted"), not sent again, and a push that fails is recorded as failed
and reported on the action. Neither is retried silently."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.actions.service import ExecutionError
from jarvis_live.config import Settings
from jarvis_live.db.models import (
    OWNER_USER_ID,
    ActionProposal,
    ActionProposalEvent,
    ScheduledReminder,
)
from jarvis_live.notify.gotify import Notifier, NotifyError
from jarvis_live.workitems.template import session_url

log = logging.getLogger(__name__)

PRIORITY = 6
LATE_AFTER_S = 120  # picked up later than this after fire_at: say so


class ReminderExecutor:
    """Stores the schedule. Idempotent: one row per action, however often it runs."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        notifier: Notifier,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._notifier = notifier

    async def execute(self, proposal: ActionProposal) -> dict[str, Any]:
        # Gotify reaches the deployment owner's phone: nobody else's reminders go there.
        if proposal.user_id != OWNER_USER_ID:
            raise ExecutionError(
                "reminders are pushed to the owner's phone; this is not their session"
            )
        if not self._notifier.configured:
            raise ExecutionError("Gotify is not configured on this deployment")
        p = proposal.payload
        async with self._sm() as db:
            await db.execute(
                insert(ScheduledReminder)
                .values(
                    id=uuid.uuid4(),
                    proposal_id=proposal.id,
                    fire_at=datetime.fromisoformat(p["at"]),
                    title=p["title"],
                    body=p.get("body"),
                )
                .on_conflict_do_nothing(index_elements=["proposal_id"])
            )
            await db.commit()
        found = await self.reconcile(proposal)
        assert found is not None
        return found

    async def reconcile(self, proposal: ActionProposal) -> dict[str, Any] | None:
        async with self._sm() as db:
            row = await db.scalar(
                select(ScheduledReminder).where(ScheduledReminder.proposal_id == proposal.id)
            )
        if row is None:
            return None
        return {
            "reminder_id": str(row.id),
            "fire_at": row.fire_at.astimezone(UTC).isoformat(),
            "reminder_state": row.state,
        }


class ReminderLoop:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        notifier: Notifier,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._notifier = notifier

    async def run(self) -> None:
        await self.recover()
        while True:
            await asyncio.sleep(self._cfg.reminder_poll_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("reminder pass failed")

    async def recover(self) -> None:
        """Reminders left ``sending`` by a dead process: outcome unknown, reported, not resent."""
        async with self._sm() as db:
            ids = (
                await db.scalars(
                    select(ScheduledReminder.id).where(ScheduledReminder.state == "sending")
                )
            ).all()
        for rid in ids:
            await self._settle(rid, None, "interrupted before delivery was confirmed")

    async def tick(self, now: datetime | None = None) -> None:
        """Send every due reminder once."""
        now = now or datetime.now(UTC)
        async with self._sm() as db:
            due = (
                await db.scalars(
                    select(ScheduledReminder.id)
                    .where(ScheduledReminder.state == "pending", ScheduledReminder.fire_at <= now)
                    .order_by(ScheduledReminder.fire_at)
                )
            ).all()
        for rid in due:
            await self._fire(rid, now)

    async def _fire(self, rid: uuid.UUID, now: datetime) -> None:
        async with self._sm() as db:
            claimed = await db.execute(
                update(ScheduledReminder)
                .where(ScheduledReminder.id == rid, ScheduledReminder.state == "pending")
                .values(state="sending", attempted_at=now)
            )
            await db.commit()
            if not claimed.rowcount:  # type: ignore[attr-defined]
                return  # another pass has it
            row = await db.get(ScheduledReminder, rid)
            assert row is not None
            proposal = await db.get(ActionProposal, row.proposal_id)
            assert proposal is not None
        title, message, url = self._message(row, proposal, now)
        try:
            await self._notifier.send(title, message, PRIORITY, url)
        except NotifyError as e:
            await self._settle(rid, None, str(e))
        else:
            await self._settle(rid, now, None)

    def _message(
        self, row: ScheduledReminder, proposal: ActionProposal, now: datetime
    ) -> tuple[str, str, str | None]:
        message = row.body or row.title
        if (now - row.fire_at).total_seconds() > LATE_AFTER_S:
            when = row.fire_at.astimezone(ZoneInfo(self._cfg.timezone)).strftime("%Y-%m-%d %H:%M")
            message = f"_Was due {when}._\n\n{message}"
        t_ms = proposal.evidence[0]["start_ms"] if proposal.evidence else None
        return (
            f"Reminder: {row.title}",
            message,
            session_url(self._cfg, str(proposal.session_id), t_ms) or None,
        )

    async def _settle(self, rid: uuid.UUID, sent_at: datetime | None, error: str | None) -> None:
        """Record the outcome on the reminder and on its action (result and audit log)."""
        async with self._sm() as db:
            row = await db.get(ScheduledReminder, rid)
            assert row is not None
            row.state = "sent" if error is None else "failed"
            row.sent_at, row.error = sent_at, error
            proposal = await db.get(ActionProposal, row.proposal_id)
            if proposal is not None:
                patch: dict[str, Any] = {"reminder_state": row.state}
                if error is not None:
                    patch["reminder_error"] = error
                proposal.result = {**(proposal.result or {}), **patch}
                proposal.version += 1
                proposal.updated_at = func.now()
                db.add(
                    ActionProposalEvent(
                        proposal_id=proposal.id,
                        actor_kind="system",
                        event="reminder_sent" if error is None else "reminder_failed",
                        payload={"error": error} if error else {},
                    )
                )
            await db.commit()
