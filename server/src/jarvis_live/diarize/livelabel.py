"""Gives each transcribed segment of a running session a provisional speaker.

Strictly best effort and off to the side: segments are saved and published first, and this runs
behind them with one worker and a short queue. A slow or dead sidecar means fewer labels, never a
later transcript. Everything it writes is revision 1, which the authoritative pass replaces."""

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import SegmentRow, SessionSpeaker, SpeakerProfile
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.diarize.client import DiarizeUnavailable, Embedder
from jarvis_live.diarize.live import LiveSpeakers, LiveVoice, ProfileRef
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import SpeakerAssignment, SpeakerInfo, SpeakerUpdate

LIVE_REV = 1


@dataclass(frozen=True)
class _Job:
    segment_id: uuid.UUID
    channel: str
    pcm: bytes
    speech_ms: int


class LiveLabeler:
    def __init__(
        self,
        session_id: uuid.UUID,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        embedder: Embedder,
    ) -> None:
        self._session_id = session_id
        self._cfg = settings
        self._sm = sessionmaker
        self._bus = bus
        self._embedder = embedder
        self._log = session_logger(__name__, session_id)
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(settings.speaker_live_queue)
        self._tracker: LiveSpeakers | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def submit(self, segment_id: uuid.UUID, channel: str, pcm: bytes, speech_ms: int) -> None:
        """Queue a saved segment for a label. Dropped when too short or the queue is full."""
        if speech_ms < self._cfg.speaker_live_min_ms:
            return
        try:
            self._queue.put_nowait(_Job(segment_id, channel, pcm, speech_ms))
        except asyncio.QueueFull:
            self._log.info("provisional speaker queue full, segment left unlabelled")

    async def drain(self) -> None:
        """Wait (a little) for the queued labels, e.g. at the end of the stream."""
        try:
            await asyncio.wait_for(self._queue.join(), self._cfg.speaker_live_drain_s)
        except TimeoutError:
            self._log.warning("provisional speakers still pending at the end of the stream")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._label(job)
            except Exception:
                self._log.exception("provisional speaker failed")
            finally:
                self._queue.task_done()

    async def _tracker_for_user(self) -> LiveSpeakers:
        if self._tracker is None:
            async with self._sm() as db:
                row = await db.get(SessionRow, self._session_id)
                profiles = (
                    (
                        await db.execute(
                            select(SpeakerProfile).where(
                                SpeakerProfile.user_id == (row.user_id if row else None)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            self._tracker = LiveSpeakers(
                [
                    ProfileRef(str(p.id), p.label, p.is_owner, p.model_id, list(p.embedding))
                    for p in profiles
                ],
                cluster_threshold=self._cfg.speaker_live_cluster_threshold,
                match_threshold=self._cfg.speaker_match_threshold,
                match_margin=self._cfg.speaker_match_margin,
            )
        return self._tracker

    async def _label(self, job: _Job) -> None:
        try:
            emb = await self._embedder.embed(job.pcm)
        except DiarizeUnavailable as e:
            self._log.debug("no voiceprint for a segment: %s", e)
            return
        tracker = await self._tracker_for_user()
        assigned = tracker.assign(job.channel, emb.vector, emb.model_id)
        if assigned is None:
            return
        voice, is_new = assigned
        async with self._sm() as db:
            # Rev 0 only: the authoritative pass (rev 2+) must never be overwritten by this.
            done = await db.execute(
                update(SegmentRow)
                .where(SegmentRow.id == job.segment_id, SegmentRow.speaker_rev < LIVE_REV)
                .values(speaker_id=voice.id, speaker_rev=LIVE_REV)
            )
            if not done.rowcount:  # type: ignore[attr-defined]
                await db.rollback()
                return
            await self._save_voice(db, voice, is_new)
            await db.commit()
        self._bus.publish(
            str(self._session_id),
            SpeakerUpdate(
                speakers=[
                    SpeakerInfo(
                        id=voice.id,
                        label=voice.label,
                        enrolled=voice.profile_id is not None,
                        is_owner=voice.is_owner,
                    )
                ]
                if is_new
                else [],
                assignments=[
                    SpeakerAssignment(
                        segment_id=str(job.segment_id),
                        speaker_id=voice.id,
                        rev=LIVE_REV,
                        final=False,
                    )
                ],
            ),
        )

    async def _save_voice(self, db: AsyncSession, voice: LiveVoice, is_new: bool) -> None:
        if is_new:
            ordinal = (
                await db.scalar(
                    select(func.coalesce(func.max(SessionSpeaker.ordinal), 0)).where(
                        SessionSpeaker.session_id == self._session_id
                    )
                )
                or 0
            ) + 1
            row = {
                "session_id": self._session_id,
                "speaker_id": voice.id,
                "channel": voice.channel,
                "ordinal": ordinal,
                "label": voice.label,
                "is_owner": voice.is_owner,
                "embedding": voice.embedding,
                "model_id": voice.model_id,
                "profile_id": uuid.UUID(voice.profile_id) if voice.profile_id else None,
            }
            # A restarted server forgets its voices and starts numbering again: take over the row.
            await db.execute(
                insert(SessionSpeaker)
                .values(**row)
                .on_conflict_do_update(
                    index_elements=["session_id", "speaker_id"],
                    set_={k: v for k, v in row.items() if k not in ("session_id", "speaker_id")},
                )
            )
        else:
            await db.execute(
                update(SessionSpeaker)
                .where(
                    SessionSpeaker.session_id == self._session_id,
                    SessionSpeaker.speaker_id == voice.id,
                )
                .values(embedding=voice.embedding)
            )
