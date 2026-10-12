"""Reviewing work items: edit, approve, discard, restore, merge, copy, and the dispatch gate.

Only the session's owner sees and acts on its work items (the ideas pass runs only for the
owner's and admins' sessions). Every change runs in a transaction holding the item's row lock,
the same lock the ideas pass takes, so an edit and an LLM update cannot interleave. Every change
is written to ``work_item_events``."""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.models import WorkItem, WorkItemEvent
from jarvis_live.workitems.dispatch import Actor, Dispatcher, refusal, sha256
from jarvis_live.workitems.rows import log_event
from jarvis_live.workitems.state import (
    EDITABLE_FIELDS,
    MAX_DONE_WHEN,
    MAX_INTENT,
    MAX_TITLE,
    clean_criteria,
    clip,
    clip_block,
)
from jarvis_live.workitems.template import (
    PromptInput,
    PromptTemplate,
    TemplateError,
    find_template,
    render_prompt,
)

log = logging.getLogger(__name__)

MAX_GUIDANCE = 4000


class WorkItemError(Exception):
    """A refused action; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Listed:
    item: WorkItem
    session_title: str | None
    session_started_at: datetime


def approval_problems(item: WorkItem, allowlist: dict[str, str]) -> list[str]:
    """What stands between a draft and approval, in words the dashboard shows."""
    problems = []
    if not item.repo:
        problems.append("choose a repository")
    elif item.repo not in allowlist:
        problems.append(f"{item.repo} is not on the allowlist")
    if not item.title.strip():
        problems.append("add a title")
    if not item.acceptance_criteria:
        problems.append("add at least one acceptance criterion")
    return problems


def _touch(item: WorkItem) -> None:
    """Bump the version the dashboard polls on. ``updated_at`` is set here rather than by the
    column's ``onupdate``, which would expire it and break reading it after the commit."""
    item.version += 1
    item.updated_at = datetime.now(UTC)


class WorkItemService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._dispatcher = dispatcher
        # Checked at startup when work items are on: a broken template fails fast.
        self.template: PromptTemplate | None = (
            PromptTemplate.load(find_template(settings)) if settings.work_items_enabled else None
        )

    @property
    def enabled(self) -> bool:
        return self.template is not None

    @property
    def allowlist(self) -> dict[str, str]:
        return self._cfg.work_repo_allowlist

    @property
    def dispatch_enabled(self) -> bool:
        return self._dispatcher is not None

    # --- reading ----------------------------------------------------------------------------

    async def for_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> list[WorkItem]:
        async with self._sm() as db:
            rows = await db.execute(
                select(WorkItem)
                .where(WorkItem.session_id == session_id, WorkItem.user_id == user_id)
                .order_by(WorkItem.created_at, WorkItem.ref)
            )
            return list(rows.scalars().all())

    async def for_user(self, user_id: uuid.UUID, states: Sequence[str]) -> list[Listed]:
        async with self._sm() as db:
            rows = await db.execute(
                select(WorkItem, SessionRow.title, SessionRow.started_at)
                .join(SessionRow, SessionRow.id == WorkItem.session_id)
                .where(WorkItem.user_id == user_id, WorkItem.state.in_(states))
                .order_by(SessionRow.started_at.desc(), WorkItem.created_at)
                .limit(500)
            )
            return [Listed(i, t, s) for i, t, s in rows.all()]

    async def get(self, item_id: uuid.UUID, user_id: uuid.UUID) -> WorkItem:
        async with self._sm() as db:
            return await self._own(db, item_id, user_id)

    async def events(self, item_id: uuid.UUID, user_id: uuid.UUID) -> list[WorkItemEvent]:
        async with self._sm() as db:
            await self._own(db, item_id, user_id)
            rows = await db.execute(
                select(WorkItemEvent)
                .where(WorkItemEvent.work_item_id == item_id)
                .order_by(WorkItemEvent.id)
            )
            return list(rows.scalars().all())

    async def prompt(self, item_id: uuid.UUID, user_id: uuid.UUID) -> tuple[str, bool]:
        """The prompt: the frozen one once approved, otherwise a live preview."""
        async with self._sm() as db:
            item = await self._own(db, item_id, user_id)
            if item.state == "approved" and item.prompt_md is not None:
                return item.prompt_md, True
            return await self._render(db, item), False

    # --- changing ---------------------------------------------------------------------------

    def require_enabled(self) -> None:
        if not self.enabled:
            raise WorkItemError(503, "work items are off on this server")

    async def edit(self, item_id: uuid.UUID, actor: Actor, fields: dict[str, Any]) -> WorkItem:
        async with self._sm() as db, db.begin():
            item = await self._own(db, item_id, actor.user_id, lock=True)
            if item.state not in ("draft", "approved"):
                raise WorkItemError(409, f"a {item.state} work item cannot be edited")
            changed = self._apply_edit(item, fields)
            if not changed:
                return item
            item.locked_fields = sorted(set(item.locked_fields) | set(changed))
            if item.state == "approved":
                self._unfreeze(item)
                log_event(db, item.id, "unapproved", **self._who(actor), payload={"why": "edited"})
            _touch(item)
            log_event(db, item.id, "edited", **self._who(actor), payload={"fields": changed})
        return item

    async def approve(self, item_id: uuid.UUID, actor: Actor) -> WorkItem:
        if actor.kind != "web":
            raise WorkItemError(403, "approve from the dashboard sign-in, not a device token")
        async with self._sm() as db, db.begin():
            item = await self._own(db, item_id, actor.user_id, lock=True)
            if item.state != "draft":
                raise WorkItemError(409, f"only drafts can be approved; this one is {item.state}")
            if problems := approval_problems(item, self.allowlist):
                raise WorkItemError(409, "cannot approve yet: " + "; ".join(problems))
            prompt = await self._render(db, item)
            item.state = "approved"
            item.prompt_md = prompt
            item.prompt_sha256 = sha256(prompt)
            item.approved_by = actor.user_id
            item.approved_at = datetime.now(UTC)
            _touch(item)
            log_event(
                db,
                item.id,
                "approved",
                **self._who(actor),
                payload={"repo": item.repo, "prompt": prompt, "prompt_sha256": item.prompt_sha256},
            )
        return item

    async def unapprove(self, item_id: uuid.UUID, actor: Actor) -> WorkItem:
        async with self._sm() as db, db.begin():
            item = await self._own(db, item_id, actor.user_id, lock=True)
            if item.state != "approved":
                raise WorkItemError(409, "the work item is not approved")
            self._unfreeze(item)
            _touch(item)
            log_event(db, item.id, "unapproved", **self._who(actor))
        return item

    async def discard(self, item_id: uuid.UUID, actor: Actor) -> WorkItem:
        async with self._sm() as db, db.begin():
            item = await self._own(db, item_id, actor.user_id, lock=True)
            if item.state not in ("draft", "approved"):
                raise WorkItemError(409, f"a {item.state} work item cannot be discarded")
            self._unfreeze(item)
            item.state = "discarded"
            _touch(item)
            log_event(db, item.id, "discarded", **self._who(actor))
        return item

    async def restore(self, item_id: uuid.UUID, actor: Actor) -> WorkItem:
        async with self._sm() as db, db.begin():
            item = await self._own(db, item_id, actor.user_id, lock=True)
            if item.state != "discarded":
                raise WorkItemError(409, "only discarded work items can be restored")
            item.state = "draft"
            _touch(item)
            log_event(db, item.id, "restored", **self._who(actor))
        return item

    async def merge(
        self, into_id: uuid.UUID, from_ids: Sequence[uuid.UUID], actor: Actor
    ) -> WorkItem:
        """Fold drafts of the same session into one: sources and criteria are unioned, intents
        appended. The others become ``merged`` and point at the result."""
        others = [i for i in dict.fromkeys(from_ids) if i != into_id]
        if not others:
            raise WorkItemError(422, "name at least one other work item to merge")
        async with self._sm() as db, db.begin():
            ids = sorted([into_id, *others])  # a stable lock order
            rows = {
                r.id: r
                for r in (
                    await db.execute(
                        select(WorkItem)
                        .where(WorkItem.id.in_(ids), WorkItem.user_id == actor.user_id)
                        .order_by(WorkItem.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            }
            if len(rows) != len(ids):
                raise WorkItemError(404, "work item not found")
            into = rows[into_id]
            srcs = [rows[i] for i in others]
            if any(r.session_id != into.session_id for r in srcs):
                raise WorkItemError(409, "only work items of the same session can be merged")
            if any(r.state != "draft" for r in [into, *srcs]):
                raise WorkItemError(409, "only drafts can be merged")
            seen = {s["segment_id"] for s in into.sources}
            sources = list(into.sources)
            for r in srcs:
                sources += [s for s in r.sources if s["segment_id"] not in seen]
                seen |= {s["segment_id"] for s in r.sources}
                r.state, r.merged_into = "merged", into.id
                _touch(r)
                log_event(db, r.id, "merged", **self._who(actor), payload={"into": str(into.id)})
            into.sources = sorted(sources, key=lambda s: (s["start_ms"], s["end_ms"]))
            into.acceptance_criteria = clean_criteria(
                [*into.acceptance_criteria, *(c for r in srcs for c in r.acceptance_criteria)]
            )
            intents = [into.intent, *(r.intent for r in srcs)]
            into.intent = clip_block("\n\n".join(i for i in intents if i.strip()), MAX_INTENT)
            if into.repo is None:
                into.repo = next((r.repo for r in srcs if r.repo), None)
            # The owner chose this result: the LLM no longer rewrites it.
            into.locked_fields = sorted(set(into.locked_fields) | set(EDITABLE_FIELDS))
            _touch(into)
            log_event(
                db,
                into.id,
                "merged",
                **self._who(actor),
                payload={"from": [str(i) for i in others]},
            )
        return into

    async def copied(self, item_id: uuid.UUID, actor: Actor) -> None:
        async with self._sm() as db, db.begin():
            item = await self._own(db, item_id, actor.user_id)
            log_event(
                db,
                item.id,
                "prompt_copied",
                **self._who(actor),
                payload={"prompt_sha256": item.prompt_sha256, "frozen": item.state == "approved"},
            )

    async def dispatch(
        self, item_id: uuid.UUID, actor: Actor, guidance: str | None = None
    ) -> WorkItem:
        """Hand an approved item to the dispatcher, after the gate. Refusals are logged too.
        ``guidance``: the owner's reply to the last run, which a follow-up run continues with.
        It goes through the same gate: it steers the approved prompt, never replaces it."""
        if guidance is not None:
            guidance = guidance.strip()
            if not guidance:
                raise WorkItemError(422, "the guidance is empty")
            if len(guidance) > MAX_GUIDANCE:
                raise WorkItemError(422, f"the guidance is longer than {MAX_GUIDANCE} characters")
        async with self._sm() as db, db.begin():
            item = await self._own(db, item_id, actor.user_id, lock=True)
            reason = refusal(item, actor, self.allowlist, self._cfg.dispatch_repo_allowlist)
            status = 409
            if reason is None and self._dispatcher is None:
                reason, status = "dispatch is not configured on this server", 503
            if reason is not None:
                log_event(
                    db, item.id, "dispatch_refused", **self._who(actor), payload={"reason": reason}
                )
            else:
                # What the gate just checked, captured under the lock: an edit after this
                # commit cannot change what is dispatched.
                repo, prompt, digest = item.repo, item.prompt_md, item.prompt_sha256
                log_event(
                    db,
                    item.id,
                    "dispatch_requested",
                    **self._who(actor),
                    payload={
                        "repo": repo,
                        "prompt": prompt,
                        "prompt_sha256": digest,
                        **({"guidance": guidance} if guidance is not None else {}),
                    },
                )
        if reason is not None:
            raise WorkItemError(status, reason)
        assert self._dispatcher is not None and repo and prompt and digest
        try:
            await self._dispatcher.dispatch(
                item.id,
                repo=repo,
                prompt=prompt,
                prompt_sha256=digest,
                actor_user_id=actor.user_id,
                **({"guidance": guidance} if guidance is not None else {}),
            )
        except WorkItemError as e:  # e.g. already running: refused, and said so in the log
            async with self._sm() as db, db.begin():
                log_event(
                    db,
                    item.id,
                    "dispatch_refused",
                    **self._who(actor),
                    payload={"reason": e.message},
                )
            raise
        return item

    # --- helpers ----------------------------------------------------------------------------

    async def _own(
        self, db: AsyncSession, item_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
    ) -> WorkItem:
        q = select(WorkItem).where(WorkItem.id == item_id)
        if lock:
            q = q.with_for_update()
        item = (await db.execute(q)).scalar_one_or_none()
        if item is None or item.user_id != user_id:
            raise WorkItemError(404, "work item not found")
        return item

    async def _render(self, db: AsyncSession, item: WorkItem) -> str:
        if self.template is None:
            raise WorkItemError(503, "work items are off on this server")
        session = await db.get(SessionRow, item.session_id)
        assert session is not None
        try:
            return render_prompt(
                self.template,
                PromptInput(
                    session_id=str(item.session_id),
                    session_title=session.title,
                    started_at=session.started_at,
                    ref=item.ref,
                    title=item.title,
                    repo=item.repo,
                    intent=item.intent,
                    acceptance_criteria=list(item.acceptance_criteria),
                    done_when=item.done_when,
                    sources=list(item.sources),
                ),
                self._cfg,
            )
        except TemplateError as e:
            log.exception("work item prompt failed to render")
            raise WorkItemError(500, str(e)) from e

    def _apply_edit(self, item: WorkItem, fields: dict[str, Any]) -> list[str]:
        changed: list[str] = []
        for name, value in fields.items():
            if name == "title":
                value = clip(value, MAX_TITLE)
                if not value:
                    raise WorkItemError(422, "the title cannot be empty")
            elif name == "repo":
                value = (value or "").strip() or None
                if value is not None and value not in self.allowlist:
                    raise WorkItemError(422, f"{value} is not on the allowlist")
            elif name == "intent":
                value = clip_block(value, MAX_INTENT)
            elif name == "acceptance_criteria":
                value = clean_criteria(list(value or []))
            elif name == "done_when":
                value = clip(value, MAX_DONE_WHEN) or None
            else:
                raise WorkItemError(422, f"{name} cannot be edited")
            if getattr(item, name) != value:
                setattr(item, name, value)
                changed.append(name)
        return changed

    @staticmethod
    def _unfreeze(item: WorkItem) -> None:
        if item.state == "approved":
            item.state = "draft"
        item.prompt_md = item.prompt_sha256 = None
        item.approved_by = item.approved_at = None

    @staticmethod
    def _who(actor: Actor) -> dict[str, Any]:
        return {"actor_user_id": actor.user_id, "actor_kind": actor.kind}


__all__ = ["Listed", "WorkItemError", "WorkItemService", "approval_problems"]
