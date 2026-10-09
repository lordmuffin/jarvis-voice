"""Audio retention: delete old ``*.pcm`` files of finished sessions. Rows are kept."""

import asyncio
import logging
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.db.models import Session

log = logging.getLogger(__name__)


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


async def run_retention_loop(
    sessionmaker: "async_sessionmaker[AsyncSession]",
    data_dir: Path,
    retention_days: int,
    interval_s: float = 86_400.0,
) -> None:
    while True:
        try:
            removed = await purge_expired_audio(sessionmaker, data_dir, retention_days)
            log.info("retention sweep removed %d audio files", removed)
        except Exception:
            log.exception("retention sweep failed")  # keep the daily loop alive
        await asyncio.sleep(interval_s)
