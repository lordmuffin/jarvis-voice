"""Reviewing and executing action proposals.

Nothing runs unless the session's owner approved it: ``execute_approved`` is the only caller of
an executor, and it only picks up rows in the ``approved`` state. Every transition is a
compare-and-set on the row's state, so a double tap, a retried request or two concurrent
executions cannot run an action twice. A failure is recorded and reported; it is retried only
when the owner asks (``retry``). Every change is written to ``action_proposal_events``.

Executions of one session run one at a time, in ``position`` order, under that session's lock
(single replica, so an in-process lock suffices)."""

import asyncio
import logging
import uuid
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.state import Context, Proposal, build_payload, dedupe_key, payload_sha256
from jarvis_live.config import Settings
from jarvis_live.db.models import ActionProposal, ActionProposalEvent
from jarvis_live.workitems.dispatch import Actor

log = logging.getLogger(__name__)

EDITABLE: dict[str, tuple[str, ...]] = {
    "task.create": ("text", "due"),
    "vault.append": ("text",),
    "reminder.schedule": ("title", "body", "at"),
    "git.pr_comment": ("body",),
    "pi.session": ("title", "text"),
}
# An agent with a shell, so the rule work items follow, not the device approval of plan D3:
# a leaked phone token must not be able to start one.
WEB_APPROVAL_KINDS = frozenset({"pi.session"})
Outcome = Literal["approved", "already", "stale", "not_open", "not_found", "needs_web"]


class ActionError(Exception):
    """A refused action; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ExecutionError(Exception):
    """An executor's reportable failure; its message is shown to the owner."""


class Executor(Protocol):
    async def execute(self, proposal: ActionProposal) -> dict[str, Any]:
        """Do it, and return what happened. Must be idempotent on ``proposal.id``."""
        ...

    async def reconcile(self, proposal: ActionProposal) -> dict[str, Any] | None:
        """Read-only: did an interrupted execution land? Its result if so, else None."""
        ...


@dataclass(frozen=True)
class ApprovalOutcome:
    id: uuid.UUID
    outcome: Outcome


class ActionService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        executors: Mapping[str, Executor],
        context: Callable[[], Context],
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._executors = executors
        self._context = context
        self._locks: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    @property
    def enabled(self) -> bool:
        return self._cfg.actions_enabled

    def can_run(self, kind: str) -> bool:
        return kind in self._executors

    def lock(self, session_id: uuid.UUID) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = self._locks[session_id] = asyncio.Lock()
        return lock

    # --- reading ----------------------------------------------------------------------------

    async def for_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> list[ActionProposal]:
        async with self._sm() as db:
            rows = await db.execute(
                select(ActionProposal)
                .where(ActionProposal.session_id == session_id, ActionProposal.user_id == user_id)
                .order_by(ActionProposal.position)
            )
            return list(rows.scalars().all())

    async def get(self, proposal_id: uuid.UUID, user_id: uuid.UUID) -> ActionProposal:
        async with self._sm() as db:
            return await self._own(db, proposal_id, user_id)

    async def events(self, proposal_id: uuid.UUID, user_id: uuid.UUID) -> list[ActionProposalEvent]:
        async with self._sm() as db:
            await self._own(db, proposal_id, user_id)
            rows = await db.execute(
                select(ActionProposalEvent)
                .where(ActionProposalEvent.proposal_id == proposal_id)
                .order_by(ActionProposalEvent.id)
            )
            return list(rows.scalars().all())

    @staticmethod
    async def _own(db: AsyncSession, proposal_id: uuid.UUID, user_id: uuid.UUID) -> ActionProposal:
        row = await db.get(ActionProposal, proposal_id)
        if row is None or row.user_id != user_id:
            raise ActionError(404, "no such action")
        return row

    # --- proposing --------------------------------------------------------------------------

    async def admit(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        proposals: Sequence[Proposal],
        *,
        proposed_by: str,
    ) -> list[uuid.UUID]:
        """Store validated proposals for review. Executes nothing. One already stored (same
        content) is skipped. Returns the ids of the new rows."""
        ids: list[uuid.UUID] = []
        async with self._sm() as db:
            position = (
                await db.scalar(
                    select(func.coalesce(func.max(ActionProposal.position), 0)).where(
                        ActionProposal.session_id == session_id
                    )
                )
            ) or 0
            for p in proposals:
                new_id = await db.scalar(
                    insert(ActionProposal)
                    .values(
                        id=uuid.uuid4(),
                        session_id=session_id,
                        user_id=user_id,
                        ref=p.ref,
                        position=position + 1,
                        kind=p.kind,
                        payload=p.payload,
                        payload_sha256=p.payload_sha256,
                        dedupe_key=p.dedupe_key,
                        evidence=[e.model_dump() for e in p.evidence],
                        proposed_by=proposed_by,
                    )
                    .on_conflict_do_nothing(index_elements=["session_id", "dedupe_key"])
                    .returning(ActionProposal.id)
                )
                if new_id is None:
                    continue
                position += 1
                ids.append(new_id)
                self._log(
                    db,
                    new_id,
                    "proposed",
                    payload={"proposed_by": proposed_by, "payload_sha256": p.payload_sha256},
                )
            await db.commit()
        return ids

    # --- reviewing --------------------------------------------------------------------------

    async def edit(
        self, proposal_id: uuid.UUID, actor: Actor, changes: Mapping[str, Any]
    ) -> ActionProposal:
        """Change what will be done. Only while it is proposed; the hash changes, so an
        approval of the old content is refused."""
        async with self._sm() as db:
            row = await self._own(db, proposal_id, actor.user_id)
            if row.state != "proposed":
                raise ActionError(409, f"the action is {row.state}, not proposed")
            allowed = EDITABLE.get(row.kind, ())
            if bad := sorted(set(changes) - set(allowed)):
                raise ActionError(400, f"cannot edit {', '.join(bad)}")
            merged = {**row.payload, **changes}
            evidence = [Evidence(**e) for e in row.evidence] or [Evidence(start_ms=0, text="-")]
            new = build_payload(
                ProposalIn(kind=row.kind, evidence=evidence, **merged), self._context()
            )
            if new is None:
                raise ActionError(422, "that is not a valid action")
            before = row.payload
            sha = payload_sha256(row.kind, new)
            try:
                changed = await self._cas(
                    db,
                    proposal_id,
                    "proposed",
                    payload=new,
                    payload_sha256=sha,
                    dedupe_key=dedupe_key(row.kind, new),
                )
                if changed:
                    self._log(
                        db,
                        proposal_id,
                        "edited",
                        actor,
                        {"before": before, "after": new, "payload_sha256": sha},
                    )
                await db.commit()
            except IntegrityError as e:
                raise ActionError(409, "an identical action already exists") from e
            if not changed:
                raise ActionError(409, "the action changed; reload it")
        return await self.get(proposal_id, actor.user_id)

    async def reject(self, proposal_id: uuid.UUID, actor: Actor) -> ActionProposal:
        async with self._sm() as db:
            row = await self._own(db, proposal_id, actor.user_id)
            if row.state == "rejected":
                return row
            if not await self._cas(db, proposal_id, "proposed", state="rejected"):
                raise ActionError(409, f"the action is {row.state}, not proposed")
            self._log(db, proposal_id, "rejected", actor)
            await db.commit()
        return await self.get(proposal_id, actor.user_id)

    async def approve(
        self, session_id: uuid.UUID, actor: Actor, items: Sequence[tuple[uuid.UUID, str]]
    ) -> list[ApprovalOutcome]:
        """Approve each ``(id, payload_sha256 the reviewer saw)``. Approving only marks them;
        ``execute_approved`` runs them. Re-approving something already approved is a no-op."""
        out: list[ApprovalOutcome] = []
        blocked = () if actor.kind == "web" else (ActionProposal.kind.not_in(WEB_APPROVAL_KINDS),)
        async with self._sm() as db:
            for pid, seen_sha in items:
                result = await db.execute(
                    update(ActionProposal)
                    .where(
                        ActionProposal.id == pid,
                        ActionProposal.session_id == session_id,
                        ActionProposal.user_id == actor.user_id,
                        ActionProposal.state == "proposed",
                        ActionProposal.payload_sha256 == seen_sha,
                        *blocked,
                    )
                    .values(
                        state="approved",
                        approved_by=actor.user_id,
                        approved_at=datetime.now(UTC),
                        version=ActionProposal.version + 1,
                        updated_at=func.now(),
                    )
                )
                if result.rowcount:  # type: ignore[attr-defined]
                    self._log(db, pid, "approved", actor, {"payload_sha256": seen_sha})
                    out.append(ApprovalOutcome(pid, "approved"))
                    continue
                row = await db.get(ActionProposal, pid)
                if row is None or row.user_id != actor.user_id or row.session_id != session_id:
                    out.append(ApprovalOutcome(pid, "not_found"))
                elif row.state in ("rejected", "expired"):
                    out.append(ApprovalOutcome(pid, "not_open"))
                elif row.state == "proposed" and blocked and row.kind in WEB_APPROVAL_KINDS:
                    out.append(ApprovalOutcome(pid, "needs_web"))
                elif row.state == "proposed":
                    out.append(ApprovalOutcome(pid, "stale"))
                else:
                    out.append(ApprovalOutcome(pid, "already"))
            await db.commit()
        return out

    async def retry(self, proposal_id: uuid.UUID, actor: Actor) -> ActionProposal:
        """The owner's explicit request to run a failed action again."""
        async with self._sm() as db:
            row = await self._own(db, proposal_id, actor.user_id)
            if row.kind in WEB_APPROVAL_KINDS and actor.kind != "web":
                raise ActionError(403, "retry this from the dashboard sign-in, not a device token")
            if not await self._cas(
                db,
                proposal_id,
                "failed",
                state="approved",
                approved_by=actor.user_id,
                approved_at=datetime.now(UTC),
            ):
                raise ActionError(409, f"the action is {row.state}, not failed")
            self._log(
                db, proposal_id, "retry_requested", actor, {"payload_sha256": row.payload_sha256}
            )
            await db.commit()
        return await self.get(proposal_id, actor.user_id)

    # --- executing --------------------------------------------------------------------------

    async def execute_approved(self, session_id: uuid.UUID) -> None:
        """Run the session's approved actions, one at a time in order. A failure is recorded
        and the next one still runs."""
        async with self.lock(session_id):
            while (nxt := await self._next_approved(session_id)) is not None:
                await self._run(nxt)

    async def _next_approved(self, session_id: uuid.UUID) -> uuid.UUID | None:
        async with self._sm() as db:
            return await db.scalar(
                select(ActionProposal.id)
                .where(ActionProposal.session_id == session_id, ActionProposal.state == "approved")
                .order_by(ActionProposal.position)
                .limit(1)
            )

    async def _run(self, proposal_id: uuid.UUID) -> None:
        async with self._sm() as db:
            if not await self._cas(db, proposal_id, "approved", state="executing"):
                return
            self._log(db, proposal_id, "exec_started")
            await db.commit()
            row = await db.get(ActionProposal, proposal_id)
        assert row is not None
        executor = self._executors.get(row.kind)
        try:
            if executor is None:
                raise ExecutionError(f"{row.kind} is not available in this deployment")
            result = await executor.execute(row)
        except asyncio.CancelledError:
            raise  # shutdown: stays ``executing`` and is reconciled on the next start
        except ExecutionError as e:
            await self._finish(proposal_id, "failed", {"error": str(e)})
        except Exception as e:
            log.exception("action %s failed", proposal_id)
            await self._finish(proposal_id, "failed", {"error": f"{type(e).__name__}: {e}"})
        else:
            await self._finish(proposal_id, "succeeded", result)

    async def _finish(self, proposal_id: uuid.UUID, state: str, result: dict[str, Any]) -> None:
        async with self._sm() as db:
            await self._cas(
                db,
                proposal_id,
                "executing",
                state=state,
                result=result,
                executed_at=datetime.now(UTC),
            )
            self._log(
                db,
                proposal_id,
                "exec_succeeded" if state == "succeeded" else "exec_failed",
                payload=result,
            )
            await db.commit()

    # --- housekeeping -----------------------------------------------------------------------

    async def expire_stale(self) -> int:
        """Unreviewed proposals past the TTL expire. They never execute."""
        cutoff = datetime.now(UTC) - timedelta(days=self._cfg.actions_ttl_days)
        async with self._sm() as db:
            ids = (
                await db.scalars(
                    update(ActionProposal)
                    .where(ActionProposal.state == "proposed", ActionProposal.created_at < cutoff)
                    .values(
                        state="expired", version=ActionProposal.version + 1, updated_at=func.now()
                    )
                    .returning(ActionProposal.id)
                )
            ).all()
            for pid in ids:
                self._log(db, pid, "expired")
            await db.commit()
        return len(ids)

    async def recover(self) -> None:
        """After a restart, settle actions left ``executing``. Read-only: an action whose effect
        is found is marked succeeded, otherwise failed ("interrupted"). Nothing is written to
        a target; running it again takes the owner's explicit retry."""
        async with self._sm() as db:
            rows = list(
                (
                    await db.scalars(
                        select(ActionProposal).where(ActionProposal.state == "executing")
                    )
                ).all()
            )
        for row in rows:
            executor = self._executors.get(row.kind)
            found: dict[str, Any] | None = None
            if executor is not None:
                try:
                    found = await executor.reconcile(row)
                except Exception:
                    log.exception("reconciling action %s failed", row.id)
            if found is not None:
                await self._finish(row.id, "succeeded", found)
            else:
                await self._finish(
                    row.id,
                    "failed",
                    {"error": "interrupted, outcome unknown; retry to run it again"},
                )

    # --- helpers ----------------------------------------------------------------------------

    @staticmethod
    async def _cas(db: AsyncSession, proposal_id: uuid.UUID, expect: str, **values: Any) -> bool:
        """Update the row iff it is still in state ``expect``."""
        result = await db.execute(
            update(ActionProposal)
            .where(ActionProposal.id == proposal_id, ActionProposal.state == expect)
            .values(version=ActionProposal.version + 1, updated_at=func.now(), **values)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    def _log(
        db: AsyncSession,
        proposal_id: uuid.UUID,
        event: str,
        actor: Actor | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        db.add(
            ActionProposalEvent(
                proposal_id=proposal_id,
                actor_user_id=actor.user_id if actor else None,
                actor_kind=actor.kind if actor else "system",
                event=event,
                payload=payload or {},
            )
        )
