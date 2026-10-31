"""Lifting a session's restriction: its note leaves ``Restricted/`` for the folder an ordinary
note of that user goes to, and loses the tag, so it can be published like any other."""

import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.models import User
from jarvis_live.finalize.finalizer import write_atomic
from jarvis_live.finalize.outbox import (
    latest_final_note,
    outbox_dir,
    read_note,
    record_final_note,
    remove_superseded,
)


def unrestricted_markdown(text: str) -> str:
    """The note without the ``restricted: true`` line and tag the finalizer added. The note may
    have been edited since, so only these two lines are touched."""
    return text.replace("\nrestricted: true\n", "\n", 1).replace(", restricted]", "]", 1)


async def lift_restriction(
    cfg: Settings, sm: "async_sessionmaker[AsyncSession]", session_id: uuid.UUID
) -> None:
    """Refile the session's note (if it has one on disk), then clear the flag. The flag goes
    last, so a failure half way leaves a session that is still restricted and can be retried."""
    async with sm() as db:
        user = await db.scalar(
            select(User)
            .join(SessionRow, SessionRow.user_id == User.id)
            .where(SessionRow.id == session_id)
        )
        found = await latest_final_note(db, session_id)
    outbox = outbox_dir(cfg)
    if found is not None:
        old, title = found
        text = await asyncio.to_thread(read_note, outbox, old)
        if text is not None:
            subdir = user.notes_subdir if user else None
            written = await asyncio.to_thread(
                write_atomic,
                outbox / subdir if subdir else outbox,
                Path(old).name,
                unrestricted_markdown(text),
                str(session_id),
            )
            if subdir:
                written = f"{subdir}/{written}"
            await record_final_note(sm, session_id, written, title)
            if written != old:
                await asyncio.to_thread(remove_superseded, outbox, old, str(session_id))
    async with sm() as db:
        await db.execute(
            update(SessionRow).where(SessionRow.id == session_id).values(restricted=False)
        )
        await db.commit()
