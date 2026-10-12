"""Converting between ``WorkItem`` rows and the merge's ``Item``, and the audit log writer."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_live.db.models import WorkItem, WorkItemEvent
from jarvis_live.workitems.state import Item, Source


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
