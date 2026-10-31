"""Retention, in three independent parts, swept together once a day:

* ``purge_expired_audio``: delete old ``*.pcm`` files of finished sessions; rows are kept.
  (``audio_retention_days``)
* ``soft_delete_due``: a session whose owner set "delete after N days" goes to the trash when
  its ``purge_after`` passes.
* ``purge_trash``: a session that has been in the trash for ``trash_retention_days`` is deleted
  for good, rows and audio and note together.
"""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.db.models import (
    AudioChunk,
    CopilotItem,
    MarkerRow,
    NotePublication,
    SegmentRow,
)
from jarvis_live.db.models import Session as Session
from jarvis_live.finalize.outbox import FINAL_NOTE_KIND, note_file, owned_by
from jarvis_live.notify.gotify import Notifier

log = logging.getLogger(__name__)

# A session in one of these states is still being written; nothing deletes it.
_ACTIVE = ("live", "finalizing")
# A note with an open pull request is the owner's record there: leave it until that is settled.
_PR_IN_FLIGHT = ("open", "publishing")


def _purge_dir(directory: Path, cutoff: float) -> int:
    n = 0
    for f in directory.glob("*.pcm"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            n += 1
    return n


async def purge_expired_audio(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    data_dir: Path,
    retention_days: int,
    *,
    now: float | None = None,
) -> int:
    """Delete ``*.pcm`` older than ``retention_days`` (by mtime) for sessions in ``done``.
    Returns the number of files removed."""
    cutoff = (time.time() if now is None else now) - retention_days * 86_400
    async with sessionmaker() as db:
        ids = (await db.execute(select(Session.id).where(Session.status == "done"))).scalars().all()
    removed = 0
    for sid in ids:
        directory = data_dir / "sessions" / str(sid)
        if directory.is_dir():
            removed += await asyncio.to_thread(_purge_dir, directory, cutoff)
    return removed


async def soft_delete_due(
    sessionmaker: "async_sessionmaker[AsyncSession]", *, now: datetime | None = None
) -> int:
    """Move sessions whose "delete after" date has passed to the trash. Returns how many."""
    now = now or datetime.now(UTC)
    async with sessionmaker() as db:
        moved = (
            await db.execute(
                update(Session)
                .where(
                    Session.purge_after <= now,
                    Session.deleted_at.is_(None),
                    Session.status.not_in(_ACTIVE),
                )
                .values(deleted_at=now)
                .returning(Session.id)
            )
        ).all()
        await db.commit()
    for (sid,) in moved:
        log.info("session %s reached its delete-after date; moved to the trash", sid)
    return len(moved)


def _remove_files(data_dir: Path, outbox: Path, session_id: str, notes: list[str]) -> None:
    for name in notes:
        path = note_file(outbox, name)
        if path is not None and path.is_file() and owned_by(path, session_id):
            path.unlink()
    try:
        shutil.rmtree(data_dir / "sessions" / session_id)
    except FileNotFoundError:
        pass  # nothing was ever recorded, or an earlier attempt got this far


async def _purge_one(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    data_dir: Path,
    outbox: Path,
    session_id: "object",
    cutoff: datetime,
) -> bool:
    """Delete one expired session. The row is locked for the duration, so a restore that
    arrives meanwhile waits and then finds nothing, rather than a session without its audio.
    Files go first: if that fails the transaction rolls back and the session is still whole."""
    async with sessionmaker() as db, db.begin():
        row = (
            await db.execute(
                select(Session)
                .where(
                    Session.id == session_id,
                    Session.deleted_at.is_not(None),
                    Session.deleted_at <= cutoff,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return False  # restored, or purged by another sweep
        in_flight = await db.scalar(
            select(NotePublication.session_id).where(
                NotePublication.session_id == row.id, NotePublication.state.in_(_PR_IN_FLIGHT)
            )
        )
        if in_flight is not None:
            log.info("session %s has an open pull request; not purging yet", row.id)
            return False
        notes = [
            str(p["path"])
            for p in (
                await db.execute(
                    select(CopilotItem.payload).where(
                        CopilotItem.session_id == row.id, CopilotItem.kind == FINAL_NOTE_KIND
                    )
                )
            )
            .scalars()
            .all()
        ]
        sid = str(row.id)
        await asyncio.to_thread(_remove_files, data_dir, outbox, sid, notes)
        for model in (AudioChunk, SegmentRow, MarkerRow, CopilotItem):
            await db.execute(delete(model).where(model.session_id == row.id))
        await db.delete(row)  # work items, their events and dispatches, the publication cascade
    log.info("session %s purged from the trash", sid)
    return True


async def purge_trash(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    data_dir: Path,
    outbox: Path,
    trash_days: int,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Delete, for good, sessions that have been in the trash for ``trash_days``.
    Returns ``(purged, failed)``; a failure leaves that session whole for the next sweep."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=trash_days)
    async with sessionmaker() as db:
        due = (
            (
                await db.execute(
                    select(Session.id).where(
                        Session.deleted_at.is_not(None), Session.deleted_at <= cutoff
                    )
                )
            )
            .scalars()
            .all()
        )
    purged = failed = 0
    for sid in due:
        try:
            purged += await _purge_one(sessionmaker, data_dir, outbox, sid, cutoff)
        except Exception:
            failed += 1
            log.exception("could not purge session %s", sid)
    return purged, failed


@dataclass(frozen=True)
class SweepResult:
    audio_files: int
    soft_deleted: int
    purged: int
    failed: int


async def sweep(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    data_dir: Path,
    outbox: Path,
    *,
    audio_days: int,
    trash_days: int,
    now: datetime | None = None,
) -> SweepResult:
    now = now or datetime.now(UTC)
    audio = await purge_expired_audio(sessionmaker, data_dir, audio_days, now=now.timestamp())
    moved = await soft_delete_due(sessionmaker, now=now)
    purged, failed = await purge_trash(sessionmaker, data_dir, outbox, trash_days, now=now)
    return SweepResult(audio, moved, purged, failed)


async def run_retention_loop(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    data_dir: Path,
    retention_days: int,
    interval_s: float = 86_400.0,
    *,
    trash_days: int = 30,
    outbox: Path | None = None,
    notifier: Notifier | None = None,
) -> None:
    outbox = outbox or data_dir / "outbox"
    while True:
        problem: str | None = None
        try:
            r = await sweep(
                sessionmaker,
                data_dir,
                outbox,
                audio_days=retention_days,
                trash_days=trash_days,
            )
            log.info(
                "retention sweep: %d audio files, %d moved to trash, %d purged, %d failed",
                r.audio_files,
                r.soft_deleted,
                r.purged,
                r.failed,
            )
            if r.failed:
                problem = f"{r.failed} deleted session(s) could not be purged."
        except Exception as e:
            log.exception("retention sweep failed")  # keep the daily loop alive
            problem = f"The sweep failed: {e}"
        if problem and notifier is not None:
            await notifier.notify("Jarvis: retention problem", problem, 8)
        await asyncio.sleep(interval_s)
