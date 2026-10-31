"""Converting between ``WorkItem`` rows and the merge's ``Item``, and the audit log writer."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.models import WorkItem, WorkItemEvent
from jarvis_live.workitems.state import OPEN, Change, Item, Source


def to_item(row: WorkItem) -> Item:
    return Item(
        id=row.id,
        ref=row.ref,
        title=row.title,
        state=row.state,
        repo=row.repo,
        intent=row.intent,
        acceptance_criteria=list(row.acceptance_criteria),
        done_when=row.done_when,
        sources=[Source(**s) for s in row.sources],
        locked_fields=list(row.locked_fields),
    )


def write_item(row: WorkItem, item: Item) -> None:
    row.title = item.title
    row.repo = item.repo
    row.intent = item.intent
    row.acceptance_criteria = list(item.acceptance_criteria)
    row.done_when = item.done_when
    row.sources = [s.dump() for s in item.sources]


def log_event(
    db: AsyncSession,
    item_id: uuid.UUID,
    event: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_kind: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        WorkItemEvent(
            work_item_id=item_id,
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
            event=event,
            payload=payload or {},
        )
    )


async def store_changes(
    db: AsyncSession, session: SessionRow, current: Sequence[WorkItem], changes: Sequence[Change]
) -> None:
    """Write what ``IdeasState.apply`` decided, in the transaction that holds ``current``'s row
    locks, with its audit rows."""
    by_ref = {r.ref: r for r in current}
    for ch in changes:
        row = by_ref.get(ch.item.ref)
        if row is None:
            row = WorkItem(
                id=uuid.uuid4(),
                session_id=session.id,
                user_id=session.user_id,
                ref=ch.item.ref,
                state=OPEN,
                title=ch.item.title,
            )
            write_item(row, ch.item)
            db.add(row)
            await db.flush()
            log_event(db, row.id, "proposed", payload={"title": row.title})
        else:
            write_item(row, ch.item)
            row.version += 1
            row.updated_at = datetime.now(UTC)
            log_event(db, row.id, "llm_updated", payload={"fields": ch.fields})
