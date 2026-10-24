"""Transcribe a finished session again from its stored audio (``data/sessions/<id>/*.pcm``).

The recording runs through a fresh pipeline, the same VAD, STT and guards as a live session. Its
segments replace the session's ``final`` ones only if every segment was transcribed; otherwise
the new ones are removed and the old transcript stays."""

import asyncio
import uuid
from contextlib import ExitStack

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.api.audio import audio_available, pcm_path
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import SegmentRow
from jarvis_live.ingest.pipeline import SessionPipeline
from jarvis_live.ingest.upload import UPLOAD_MAX_BACKLOG
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import MAX_PAYLOAD
from jarvis_live.stt.backend import STTBackend, STTUnavailable, Transcription


class RetranscribeFailed(Exception):
    """The session's transcript was left as it was."""


class _CountingSTT:
    """Counts the segments no tier could transcribe (the pipeline drops those silently)."""

    def __init__(self, stt: STTBackend) -> None:
        self._stt = stt
        self.lost = 0

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        try:
            return await self._stt.transcribe(pcm, prompt)
        except STTUnavailable:
            self.lost += 1
            raise

    def current_tier(self) -> str | None:
        return self._stt.current_tier()


async def retranscribe(
    session_id: uuid.UUID,
    channels: list[str],
    *,
    settings: Settings,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    bus: Bus,
    stt: STTBackend,
) -> int:
    """Returns the number of segments in the new transcript. Raises ``RetranscribeFailed``."""
    slog = session_logger(__name__, session_id)
    recorded = [c for c in channels if audio_available(settings.data_dir, session_id, c)]
    if not recorded:
        raise RetranscribeFailed("No recording is stored for this session.")
    async with sessionmaker() as db:
        old = set(
            (
                await db.execute(
                    select(SegmentRow.id).where(
                        SegmentRow.session_id == session_id, SegmentRow.source == "final"
                    )
                )
            )
            .scalars()
            .all()
        )
    counting = _CountingSTT(stt)
    pipeline = SessionPipeline(
        session_id, recorded, settings=settings, sessionmaker=sessionmaker, bus=bus, stt=counting
    )
    pipeline.start()
    ok = False
    try:
        with ExitStack() as files:
            readers = {
                c: files.enter_context(pcm_path(settings.data_dir, session_id, c).open("rb"))
                for c in recorded
            }
            while readers:  # interleaved, so the mic/system duplicate guard sees both sides
                for c, f in list(readers.items()):
                    while pipeline.pending_segments() >= UPLOAD_MAX_BACKLOG:
                        await asyncio.sleep(0.05)
                    chunk = f.read(MAX_PAYLOAD)
                    if chunk:
                        pipeline.feed(c, chunk)
                    else:
                        del readers[c]
        await pipeline.finish()
        ok = counting.lost == 0
    finally:
        await pipeline.close()
        async with sessionmaker() as db:
            new = SegmentRow.session_id == session_id, SegmentRow.source == "final"
            if ok:
                await db.execute(delete(SegmentRow).where(*new, SegmentRow.id.in_(old)))
            else:
                await db.execute(delete(SegmentRow).where(*new, SegmentRow.id.not_in(old)))
            await db.commit()
    if not ok:
        raise RetranscribeFailed(f"{counting.lost} segment(s) could not be transcribed.")
    async with sessionmaker() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(SegmentRow)
            .where(SegmentRow.session_id == session_id, SegmentRow.source == "final")
        )
    slog.info("transcribed again", extra={"segments": count})
    return int(count or 0)
