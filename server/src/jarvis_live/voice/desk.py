"""One session's door for held asks: markers in from the producer, ``ask_reply`` out.

Built by the orchestrator only for a session the voice loop may serve (voice on, not local-only,
not restricted, the owner's or an admin's). Everything slow runs in its own task: ``feed`` and
the marker calls return at once, so the audio path never waits on Whisper or the LLM."""

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live import metrics
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.db.models import CopilotItem
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import AskBegin, AskEnd, AskPlayed, AskReply, VoiceState
from jarvis_live.voice.ask import AskAudio, EchoGuard
from jarvis_live.voice.ask_pass import AskPass, AskResult
from jarvis_live.voice.service import Queue

ECHO_PAD_MS = 300
ECHO_THRESHOLD = 0.6


class AskDesk:
    def __init__(
        self,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        runner: AskPass,
        queue: Queue | None,
        position_ms: Callable[[], int],
    ) -> None:
        self.session_id = session_id
        self._user_id = user_id
        self._cfg = settings
        self._sm = sessionmaker
        self._bus = bus
        self._runner = runner
        self._queue = queue
        self._position_ms = position_ms
        self._log = session_logger(__name__, session_id)
        self.audio = AskAudio(
            preroll_ms=settings.voice_ask_preroll_ms, max_ms=int(settings.voice_ask_max_s * 1000)
        )
        self.echo = EchoGuard(pad_ms=ECHO_PAD_MS, threshold=ECHO_THRESHOLD)
        self._held: str | None = None
        self._arrived = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._replies: dict[str, tuple[int | None, str]] = {}  # ask id -> (row id, reply)
        self._writes: set[asyncio.Task[None]] = set()

    def state(self) -> VoiceState:
        return VoiceState(available=True)

    # --- from the producer (synchronous) ---------------------------------------------------

    def feed(self, t_ms: int, pcm: bytes) -> None:
        self.audio.feed(t_ms, pcm)
        self._arrived.set()
        if self._held is not None and self.audio.complete(self._held):
            self._finish(self._held)  # held to the limit: answer what was said

    def begin(self, msg: AskBegin) -> None:
        self._cancel()  # one ask at a time: a new one replaces the last, answered or not
        self.audio.begin(msg.id, msg.t_ms)
        self._held = msg.id

    def end(self, msg: AskEnd) -> None:
        if self.audio.end(msg.id, msg.t_ms):
            self._finish(msg.id)

    def played(self, msg: AskPlayed) -> None:
        row_id, reply = self._replies.get(msg.id, (None, ""))
        if msg.state == "started":
            self.echo.add(self._position_ms(), None, reply)
            if msg.latency_ms is not None:
                metrics.voice_ask_first_audio(msg.latency_ms / 1000)
        else:
            self.echo.close(self._position_ms())
        if row_id is not None:
            self._write(row_id, msg)

    # --- views for the rest of the session ------------------------------------------------

    def windows(self) -> list[tuple[int, int | None]]:
        return self.audio.windows()

    def is_echo(self, start_ms: int, end_ms: int, text: str) -> bool:
        return self.echo.is_echo(start_ms, end_ms, text)

    async def close(self) -> None:
        self._cancel()
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)

    # --- the slow part --------------------------------------------------------------------

    def _finish(self, ask_id: str) -> None:
        self._held = None
        self._task = asyncio.create_task(self._answer(ask_id))

    def _cancel(self) -> None:
        if self._held is not None:
            self.audio.drop(self._held)
            self._held = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _answer(self, ask_id: str) -> None:
        began = time.monotonic()
        deadline = began + self._cfg.voice_ask_tail_ms / 1000
        while not self.audio.complete(ask_id) and (left := deadline - time.monotonic()) > 0:
            self._arrived.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._arrived.wait(), left)
        metrics.voice_ask_stage("wait", time.monotonic() - began)
        clip = self.audio.take(ask_id)
        try:
            if clip is None:
                result = AskResult("empty", "")
            else:
                result = await self._runner.run(
                    self.session_id, self._user_id, clip, self._queue, self.windows()
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log.exception("ask failed")
            result = AskResult("failed", "Sorry, something went wrong.")
        for stage, seconds in result.stages.items():
            metrics.voice_ask_stage(stage, seconds)
        metrics.voice_ask_outcome(result.outcome)
        self._replies[ask_id] = (result.row_id, result.text)
        self._bus.publish(
            str(self.session_id), AskReply(id=ask_id, outcome=result.outcome, text=result.text)
        )

    def _write(self, row_id: int, msg: AskPlayed) -> None:
        task = asyncio.create_task(self._store_played(row_id, msg))
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def _store_played(self, row_id: int, msg: AskPlayed) -> None:
        try:
            async with self._sm() as db, db.begin():
                row = await db.get(CopilotItem, row_id, with_for_update=True)
                if row is None:
                    return
                payload = dict(row.payload)
                payload["played"] = msg.state
                if msg.latency_ms is not None:
                    payload["latency_ms"] = msg.latency_ms
                if msg.reason is not None:
                    payload["skipped"] = msg.reason
                row.payload = payload
        except Exception:
            self._log.exception("could not record how an ask was played")
