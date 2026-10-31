"""Listens for requests addressed to Jarvis, one loop per eligible live session.

It runs next to the copilot and ideas loops, so a slow LLM call here never delays suggestions. It
reads final segments only, and only the owner's own voice: the mic channel, minus any voice
diarization told apart. One more pass runs when the session ends."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.copilot.loop import Clock, RealClock
from jarvis_live.copilot.transcript import Line, load_final_lines
from jarvis_live.db.models import CopilotItem
from jarvis_live.logging_setup import session_logger
from jarvis_live.voice.service import Queue, VoicePass
from jarvis_live.voice.wake import next_request

CURSOR = "voice_cursor"  # copilot_items kind: {"until_ms": end of the last request handled}


def own_lines(lines: Sequence[Line]) -> list[Line]:
    """The lines only the owner can have said. ``speaker_label`` is set for another voice."""
    return [x for x in lines if x.speaker == "me" and x.speaker_label is None]


class VoiceLoop:
    def __init__(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        runner: VoicePass,
        proposals: Queue | None,
        stream_ms: Callable[[], int],
        reply: Callable[[str], Awaitable[None]],
        clock: Clock | None = None,
    ) -> None:
        self.session_id = session_id
        self._user_id = user_id
        self._cfg = settings
        self._sm = sessionmaker
        self._runner = runner
        self._proposals = proposals
        self._stream_ms = stream_ms
        self._reply = reply
        self._clock = clock or RealClock()
        self._log = session_logger(__name__, session_id)
        self._cursor: int | None = None  # read from the database on first use

    async def run(self) -> None:
        while True:
            await self._clock.sleep(self._cfg.copilot_poll_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log.exception("voice tick failed")

    async def tick(self) -> None:
        await self._drain(final=False)

    async def flush(self) -> None:
        """The session ended: whatever is left is finished. Its failure never blocks the note."""
        try:
            await self._drain(final=True)
        except Exception:
            self._log.exception("final voice pass failed")

    async def _drain(self, *, final: bool) -> None:
        if self._cursor is None:
            self._cursor = await self._stored_cursor()
        while True:
            async with self._sm() as db:
                lines = own_lines(await load_final_lines(db, self.session_id, self._cursor))
            if not lines:
                return
            heard, cursor = next_request(
                lines,
                self._stream_ms(),
                int(self._cfg.voice_settle_s * 1000),
                int(self._cfg.voice_max_s * 1000),
                final=final,
            )
            if heard is None:
                self._cursor = max(self._cursor, cursor)
                return
            try:
                said = await self._runner.run(
                    self.session_id, self._user_id, heard, self._proposals
                )
            except Exception:  # told once: retrying every poll would only repeat the failure
                self._log.exception("voice request failed")
                said = "Something went wrong filing that. Nothing was started."
            # Stored before the reply: a request is filed once, even if the reply fails.
            await self._store_cursor(cursor)
            self._cursor = cursor
            if said:
                await self._reply(said)

    async def _stored_cursor(self) -> int:
        async with self._sm() as db:
            payload = (
                await db.execute(
                    select(CopilotItem.payload)
                    .where(CopilotItem.session_id == self.session_id, CopilotItem.kind == CURSOR)
                    .order_by(CopilotItem.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return int(payload["until_ms"]) if payload else -1

    async def _store_cursor(self, until_ms: int) -> None:
        async with self._sm() as db:
            db.add(
                CopilotItem(session_id=self.session_id, kind=CURSOR, payload={"until_ms": until_ms})
            )
            await db.commit()
