"""Durable audio storage: one append-only PCM file per channel plus an ``audio_chunks`` index."""

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import IO, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.db.models import AudioChunk, MarkerRow, SegmentRow
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import CHANNEL_MIC, CHANNEL_SYSTEM, DraftSegment, Frame, Marker

CHANNEL_NAMES = {CHANNEL_MIC: "mic", CHANNEL_SYSTEM: "system"}
CHANNELS = ("mic", "system")
SPEAKER = {"mic": "me", "system": "them"}


class SeqGap(Exception):
    def __init__(self, channel: str, expected: int, got: int) -> None:
        super().__init__(f"{channel}: expected seq {expected}, got {got}")
        self.channel, self.expected, self.got = channel, expected, got


class StoreClosed(Exception):
    """The session no longer accepts audio."""


class AudioStore:
    def __init__(
        self,
        session_id: uuid.UUID,
        data_dir: Path,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        fsync_interval_s: float = 1.0,
    ) -> None:
        self.session_id = session_id
        self.dir = data_dir / "sessions" / str(session_id)
        self._sm = sessionmaker
        self._fsync_interval = fsync_interval_s
        self._log = session_logger(__name__, session_id)
        self._lock = asyncio.Lock()
        self._files: dict[str, IO[bytes]] = {}
        self._offset: dict[str, int] = {}
        self._last: dict[str, int | None] = {c: None for c in CHANNELS}
        self._dirty_fsync = False
        self._last_fsync = time.monotonic()
        self._closed = False

    def path(self, channel: str) -> Path:
        return self.dir / f"{channel}.pcm"

    async def load(self) -> None:
        """Recover state from the index. Bytes past the last indexed chunk (a crash between the
        file write and the row insert) are truncated so file and index never disagree."""
        self.dir.mkdir(parents=True, exist_ok=True)
        tails: dict[str, tuple[int, int]] = {}
        async with self._sm() as db:
            for channel in CHANNELS:
                last = (
                    await db.execute(
                        select(AudioChunk.seq, AudioChunk.byte_offset, AudioChunk.byte_len)
                        .where(
                            AudioChunk.session_id == self.session_id, AudioChunk.channel == channel
                        )
                        .order_by(AudioChunk.seq.desc())
                        .limit(1)
                    )
                ).first()
                if last is not None:
                    tails[channel] = (last.seq, last.byte_offset + last.byte_len)
        for channel in CHANNELS:
            seq, end = tails.get(channel, (None, 0))
            path = self.path(channel)
            size = path.stat().st_size if path.exists() else 0
            if size < end:
                raise RuntimeError(f"{path} is {size} bytes but the index expects {end}")
            if size > end:
                self._log.warning("truncating %d orphan bytes from %s", size - end, path.name)
                os.truncate(path, end)
            self._last[channel] = seq
            self._offset[channel] = end

    def cursor(self) -> dict[str, int | None]:
        return dict(self._last)

    async def append(self, frame: Frame) -> Literal["stored", "duplicate"]:
        channel = CHANNEL_NAMES[frame.channel]
        async with self._lock:
            if self._closed:
                raise StoreClosed
            last = self._last[channel]
            expected = 0 if last is None else last + 1
            if last is not None and frame.seq <= last:
                return "duplicate"
            if frame.seq != expected:
                raise SeqGap(channel, expected, frame.seq)

            f = self._files.get(channel)
            if f is None:
                f = self._files[channel] = open(self.path(channel), "ab")  # noqa: SIM115
            offset = self._offset[channel]
            f.write(frame.pcm)
            f.flush()
            try:
                async with self._sm() as db:
                    db.add(
                        AudioChunk(
                            session_id=self.session_id,
                            channel=channel,
                            seq=frame.seq,
                            t_ms=frame.t_ms,
                            byte_offset=offset,
                            byte_len=len(frame.pcm),
                        )
                    )
                    await db.commit()
            except BaseException:
                f.truncate(offset)  # keep file and index consistent
                f.flush()
                raise
            self._offset[channel] = offset + len(frame.pcm)
            self._last[channel] = frame.seq
            self._dirty_fsync = True
        await self.maybe_fsync()
        return "stored"

    async def maybe_fsync(self, *, force: bool = False) -> None:
        """fsync dirty files, at most once per ``fsync_interval_s`` unless ``force``."""
        now = time.monotonic()
        if not self._dirty_fsync or (not force and now - self._last_fsync < self._fsync_interval):
            return
        self._dirty_fsync = False
        self._last_fsync = now
        fds = [f.fileno() for f in self._files.values()]

        def _sync() -> None:
            for fd in fds:
                os.fsync(fd)

        await asyncio.to_thread(_sync)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            await self.maybe_fsync(force=True)
            for f in self._files.values():
                f.close()
            self._files.clear()


async def save_draft(
    sessionmaker: "async_sessionmaker[AsyncSession]", session_id: uuid.UUID, msg: DraftSegment
) -> None:
    async with sessionmaker() as db:
        db.add(
            SegmentRow(
                session_id=session_id,
                channel=msg.channel,
                speaker=SPEAKER[msg.channel],
                start_ms=msg.start_ms,
                end_ms=msg.end_ms,
                text=msg.text,
                source="draft",
                stt_tier=None,
            )
        )
        await db.commit()


async def save_marker(
    sessionmaker: "async_sessionmaker[AsyncSession]", session_id: uuid.UUID, msg: Marker
) -> None:
    async with sessionmaker() as db:
        db.add(MarkerRow(session_id=session_id, t_ms=msg.t_ms, label=msg.label))
        await db.commit()
