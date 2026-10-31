"""The authoritative speaker pass: runs once a session has ended, before the note is written.

Anything that goes wrong here is logged and skipped. The transcript stays as it is (``me`` and
``them`` by channel), so a diarizer outage never fails or delays a session beyond its timeout.
"""

import asyncio
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.api.audio import audio_available, pcm_path
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import SegmentRow, SessionSpeaker, SpeakerTurn
from jarvis_live.diarize.align import Turn, assign
from jarvis_live.diarize.client import Diarizer, DiarizeUnavailable
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import BYTES_PER_MS, SpeakerAssignment, SpeakerInfo, SpeakerUpdate

FINAL_REV = 2  # live (provisional) labels are rev 1; the authoritative pass is at least this


def _ids(channel: str, turns: list[Turn]) -> dict[str, str]:
    """The model's speaker names -> ids of ours, numbered by first appearance in the channel."""
    ids: dict[str, str] = {}
    for t in sorted(turns, key=lambda t: (t.start_ms, t.end_ms)):
        ids.setdefault(t.speaker, f"spk_{channel}_{len(ids)}")
    return ids


class DiarizeService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        diarizer: Diarizer | None,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._bus = bus
        self._diarizer = diarizer

    def timeout_s(self, audio_ms: int) -> float:
        return max(
            self._cfg.diarize_timeout_min_s, self._cfg.diarize_timeout_ratio * audio_ms / 1000
        )

    async def run(self, session_id: uuid.UUID, channels: list[str]) -> bool:
        """Diarize the stored recording of each channel and label the session's segments.
        True if at least one channel was labelled. Never raises; gives up at the timeout."""
        if self._diarizer is None:
            return False
        sizes = [
            pcm_path(self._cfg.data_dir, session_id, c).stat().st_size
            for c in channels
            if audio_available(self._cfg.data_dir, session_id, c)
        ]
        timeout = self.timeout_s(max(sizes, default=0) // BYTES_PER_MS)
        try:
            return await asyncio.wait_for(self._run(session_id, channels, self._diarizer), timeout)
        except TimeoutError:
            session_logger(__name__, session_id).warning(
                "diarization timed out after %.0fs", timeout
            )
            return False

    async def _run(self, session_id: uuid.UUID, channels: list[str], diarizer: Diarizer) -> bool:
        log = session_logger(__name__, session_id)
        found: dict[str, list[Turn]] = {}
        for channel in channels:
            if not audio_available(self._cfg.data_dir, session_id, channel):
                continue
            try:
                turns = await diarizer.diarize(pcm_path(self._cfg.data_dir, session_id, channel))
            except DiarizeUnavailable as e:
                log.warning("diarization unavailable for %s: %s", channel, e)
            except Exception:
                log.exception("diarization failed for %s", channel)
            else:
                found[channel] = turns
        if not found:
            return False
        try:
            await self._store(session_id, found)
            return await self.realign(session_id)
        except Exception:
            log.exception("storing speakers failed")
            return False

    async def realign(self, session_id: uuid.UUID) -> bool:
        """Label the session's final segments from the stored turns (no diarizer call), e.g.
        after the transcript was made again. Never raises."""
        try:
            return await self._align(session_id)
        except Exception:
            session_logger(__name__, session_id).exception("aligning speakers failed")
            return False

    async def _store(self, session_id: uuid.UUID, found: dict[str, list[Turn]]) -> None:
        async with self._sm() as db:
            channels = list(found)
            await db.execute(
                delete(SpeakerTurn).where(
                    SpeakerTurn.session_id == session_id, SpeakerTurn.channel.in_(channels)
                )
            )
            await db.execute(
                delete(SessionSpeaker).where(
                    SessionSpeaker.session_id == session_id, SessionSpeaker.channel.in_(channels)
                )
            )
            rows: list[tuple[int, str, str]] = []  # (first heard, channel, speaker id)
            for channel, turns in found.items():
                ids = _ids(channel, turns)
                first: dict[str, int] = {}
                for t in turns:
                    first[ids[t.speaker]] = min(first.get(ids[t.speaker], t.start_ms), t.start_ms)
                    db.add(
                        SpeakerTurn(
                            session_id=session_id,
                            channel=channel,
                            start_ms=t.start_ms,
                            end_ms=t.end_ms,
                            speaker_id=ids[t.speaker],
                        )
                    )
                rows += [(ms, channel, sid) for sid, ms in first.items()]
            await db.flush()
            # Numbered across the whole session by first appearance, for "Speaker N".
            others = (
                await db.execute(
                    select(SessionSpeaker.ordinal).where(SessionSpeaker.session_id == session_id)
                )
            ).scalars()
            ordinal = max(others, default=0)
            for _, channel, sid in sorted(rows):
                ordinal += 1
                db.add(
                    SessionSpeaker(
                        session_id=session_id,
                        speaker_id=sid,
                        channel=channel,
                        ordinal=ordinal,
                    )
                )
            await db.commit()

    async def _align(self, session_id: uuid.UUID) -> bool:
        async with self._sm() as db:
            turns = (
                (await db.execute(select(SpeakerTurn).where(SpeakerTurn.session_id == session_id)))
                .scalars()
                .all()
            )
            if not turns:
                return False
            by_channel: dict[str, list[Turn]] = {}
            for t in turns:
                by_channel.setdefault(t.channel, []).append(
                    Turn(t.start_ms, t.end_ms, t.speaker_id)
                )
            segments = (
                (
                    await db.execute(
                        select(SegmentRow).where(
                            SegmentRow.session_id == session_id, SegmentRow.source == "final"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assignments: list[SpeakerAssignment] = []
            for seg in segments:
                if seg.channel not in by_channel:
                    continue  # that channel was not diarized: keep what it has
                speaker, mixed = assign(seg.start_ms, seg.end_ms, by_channel[seg.channel])
                rev = max(seg.speaker_rev + 1, FINAL_REV)
                await db.execute(
                    update(SegmentRow)
                    .where(SegmentRow.id == seg.id)
                    .values(speaker_id=speaker, speaker_rev=rev, mixed=mixed)
                )
                assignments.append(
                    SpeakerAssignment(
                        segment_id=str(seg.id),
                        speaker_id=speaker,
                        rev=rev,
                        final=True,
                        mixed=True if mixed else None,
                    )
                )
            speakers = (
                (
                    await db.execute(
                        select(SessionSpeaker)
                        .where(SessionSpeaker.session_id == session_id)
                        .order_by(SessionSpeaker.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            infos = [
                SpeakerInfo(id=s.speaker_id, label=s.label, enrolled=False, is_owner=s.is_owner)
                for s in speakers
            ]
            await db.commit()
        self._bus.publish(
            str(session_id),
            SpeakerUpdate(speakers=infos, assignments=assignments),
        )
        return True
