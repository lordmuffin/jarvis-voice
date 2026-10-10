"""Registry of live-session ingest state (single replica; Postgres is the source of truth)."""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import WebSocket
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import Session
from jarvis_live.ingest.pipeline import SessionPipeline
from jarvis_live.ingest.store import AudioStore
from jarvis_live.logging_setup import session_logger
from jarvis_live.stt.backend import STTBackend


@dataclass
class SessionIngest:
    session_id: uuid.UUID
    channels: list[str]
    store: AudioStore
    pipeline: SessionPipeline
    producer: WebSocket | None = None
    ended: asyncio.Event = field(default_factory=asyncio.Event)


class IngestHub:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        stt: STTBackend,
        *,
        on_start: Callable[[SessionIngest], None] | None = None,
        on_end: Callable[[uuid.UUID], None] | None = None,
    ) -> None:
        """``on_start`` fires (synchronously, under the hub lock) when a session's ingest state
        is created; ``on_end`` after a live session has been moved to ``finalizing`` and its
        pipeline drained. Both must return quickly (spawn a task)."""
        self._on_start = on_start
        self._on_end = on_end
        self._cfg = settings
        self._sm = sessionmaker
        self._bus = bus
        self._stt = stt
        self._states: dict[uuid.UUID, SessionIngest] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: uuid.UUID, channels: list[str]) -> SessionIngest:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                store = AudioStore(
                    session_id, self._cfg.data_dir, self._sm, self._cfg.fsync_interval_s
                )
                await store.load()
                pipeline = SessionPipeline(
                    session_id,
                    channels,
                    settings=self._cfg,
                    sessionmaker=self._sm,
                    bus=self._bus,
                    stt=self._stt,
                )
                pipeline.start()
                state = self._states[session_id] = SessionIngest(
                    session_id, channels, store, pipeline
                )
                if self._on_start is not None:
                    self._on_start(state)
            return state

    def live_sessions(self) -> list[SessionIngest]:
        return list(self._states.values())

    def is_streaming(self, session_id: uuid.UUID) -> bool:
        """True while a producer is connected and sending audio. A ``live`` session without
        this is waiting for its producer to reconnect, or for the idle watchdog to end it."""
        state = self._states.get(session_id)
        return (
            state is not None
            and state.producer is not None
            and time.monotonic() - state.pipeline.last_audio_at < self._cfg.streaming_stale_s
        )

    async def end(self, session_id: uuid.UUID, *, caller: WebSocket | None = None) -> bool:
        """Move a live session to ``finalizing`` and drain its pipeline. Idempotent.

        Returns True if this call performed the transition. A producer other than ``caller`` is
        disconnected."""
        log = session_logger(__name__, session_id)
        async with self._sm() as db:
            result = await db.execute(
                update(Session)
                .where(Session.id == session_id, Session.status == "live")
                .values(status="finalizing", ended_at=datetime.now(UTC))
            )
            await db.commit()
        transitioned = bool(result.rowcount)  # type: ignore[attr-defined]
        async with self._lock:
            state = self._states.pop(session_id, None)
        if state is not None:
            state.ended.set()
            await state.store.close()  # reject further frames, fsync
            await state.pipeline.finish()
            await state.pipeline.close()
            if state.producer is not None and state.producer is not caller:
                await state.producer.close(code=4410)
        if transitioned:
            log.info("session ended")
            if self._on_end is not None:
                self._on_end(session_id)
        return transitioned

    async def shutdown(self) -> None:
        async with self._lock:
            states, self._states = list(self._states.values()), {}
        for s in states:
            await s.store.close()
            await s.pipeline.close()
