"""Streams pushed into MediaMTX (OBS, phones) become live sessions.

For each path with a publisher and an ingest key, this runs what the Linux capture client runs
for a microphone, inside the server:

- one ``Capturer`` per audio track, reading the path from MediaMTX with ffmpeg
  (``streams.decode``): track 1 is the mic ("me"), track 2, if the stream has one, is the system
  channel ("them");
- one ``ProducerConnection`` to this server's own producer WebSocket, with an outbox under
  ``state_dir``. Every rule of the wire protocol (contiguous seqs, ``hello``/``resume``,
  cumulative ``ack``, ``end``) applies as it does to any other client.

Lifecycle, polled from MediaMTX's API:

- **start**: a path comes online with at least one audio track: a session is created with the
  key's profile (title, mode, restricted) and its ``source`` (``obs``, ``mobile``).
- **drop**: the path goes offline. The capturers keep retrying the read; if the publisher is
  back within ``stop_grace_s`` it is the same session, with ``t_ms`` skipping the gap.
- **stop**: offline for ``stop_grace_s``: the reads stop and ``end`` is sent, so the final note
  runs.

A stream cannot be local-only: it has already left the device that made it.
"""

import asyncio
import contextlib
import logging
import shutil
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from jarvis_live.client.capture import CaptureError, Capturer, RestartPolicy
from jarvis_live.client.outbox import Outbox
from jarvis_live.client.transport import ProducerConnection, ReconnectPolicy
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.streams import keys
from jarvis_live.streams.decode import ffmpeg_command

log = logging.getLogger(__name__)

CHANNELS = ("mic", "system")
# While the publisher is away a read fails at once; retry every couple of seconds, indefinitely:
# the grace period, not the capturer, decides when the stream is over.
READ_RETRY = RestartPolicy(initial_delay=0.5, max_delay=2.0, max_rapid=sys.maxsize)

TicketFor = Callable[[uuid.UUID, uuid.UUID], Awaitable[str]]


class Paths(Protocol):
    async def online_paths(self) -> dict[str, int]: ...

    def read_url(self, name: str) -> str: ...


@dataclass
class ActiveStream:
    name: str
    session_id: uuid.UUID
    conn: ProducerConnection
    outbox: Outbox
    last_online: float
    readers: list[asyncio.Task[None]] = field(default_factory=list)


class StreamIngest:
    def __init__(
        self,
        sm: keys.SM,
        paths: Paths,
        *,
        server_url: str,
        ticket_for: TicketFor,
        state_dir: Path,
        stop_grace_s: float = 60.0,
        max_streams: int = 8,
        poll_s: float = 1.0,
        ffmpeg: str = "ffmpeg",
        reader_argv: Callable[[str, int], list[str]] | None = None,
        reconnect: ReconnectPolicy = ReconnectPolicy(),  # noqa: B008
        end_timeout: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sm = sm
        self._paths = paths
        self._server_url = server_url
        self._ticket_for = ticket_for
        self.state_dir = state_dir
        self._grace = stop_grace_s
        self._max = max_streams
        self._poll = poll_s
        self._ffmpeg = ffmpeg
        self._argv = reader_argv or self._read_argv
        self._reconnect = reconnect
        self._end_timeout = end_timeout
        self._clock = clock
        self.active: dict[str, ActiveStream] = {}
        self._ending: set[asyncio.Task[None]] = set()
        self._skipped: set[str] = set()  # online paths already logged as not ours

    def has_room(self, path: str) -> bool:
        """May ``path`` publish? A stream already running may always reconnect."""
        return path in self.active or len(self.active) < self._max

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  MediaMTX away: keep what runs, try again
                log.warning("stream ingest: cannot list MediaMTX paths", exc_info=True)
            await asyncio.sleep(self._poll)

    async def tick(self) -> None:
        online = await self._paths.online_paths()
        now = self._clock()
        for name, audio_tracks in online.items():
            if name in self.active:
                self.active[name].last_online = now
            elif audio_tracks > 0 and len(self.active) < self._max:
                await self._start(name, audio_tracks, now)
        self._skipped &= online.keys()
        for name, stream in list(self.active.items()):
            if name not in online and now - stream.last_online >= self._grace:
                del self.active[name]
                task = asyncio.create_task(self._finish(stream))
                self._ending.add(task)
                task.add_done_callback(self._ending.discard)

    async def shutdown(self) -> None:
        """Stop everything without ending a session (a server restart). The outboxes stay on
        disk; the watchdog ends sessions whose producer never returns."""
        for task in list(self._ending):
            task.cancel()
        streams, self.active = list(self.active.values()), {}
        for stream in streams:
            await self._stop_readers(stream)
            await stream.conn.stop()
            stream.outbox.close()
        await asyncio.gather(*self._ending, return_exceptions=True)

    # --- one stream ---------------------------------------------------------------------------

    def _read_argv(self, name: str, track: int) -> list[str]:
        return ffmpeg_command(self._paths.read_url(name), track=track, ffmpeg=self._ffmpeg)

    async def _start(self, name: str, audio_tracks: int, now: float) -> None:
        key = await keys.active_key(self._sm, name)
        if key is None:
            if name not in self._skipped:
                log.warning("stream ingest: %s is online but has no ingest key", name)
                self._skipped.add(name)
            return
        channels = list(CHANNELS[: min(audio_tracks, 2)])
        row = SessionRow(
            device_id=None,
            user_id=key.user_id,
            title=key.title,
            mode=key.mode,
            status="live",
            channels=channels,
            restricted=key.restricted,
            source=key.source,
        )
        async with self._sm() as db:
            db.add(row)
            await db.commit()
        sid, user_id = row.id, key.user_id
        outbox = Outbox(str(sid), self.state_dir)

        async def ticket() -> str:
            return await self._ticket_for(sid, user_id)

        conn = ProducerConnection(
            self._server_url,
            "",
            str(sid),
            outbox,
            device=f"stream:{name}",
            reconnect=self._reconnect,
            ticket_source=ticket,
        )
        stream = ActiveStream(name, sid, conn, outbox, last_online=now)
        conn.start()
        for track, channel in enumerate(channels):
            capturer = Capturer(
                self._argv(name, track),
                channel,
                outbox,
                restart=READ_RETRY,
                on_frame=lambda _f: conn.kick(),
            )
            stream.readers.append(asyncio.create_task(self._read(name, capturer)))
        self.active[name] = stream
        log.info(
            "stream %s: session %s started",
            name,
            sid,
            extra={"channels": channels, "source": key.source},
        )

    async def _read(self, name: str, capturer: Capturer) -> None:
        try:
            await capturer.run()
        except CaptureError:
            log.exception("stream %s: cannot read %s", name, capturer.channel)

    async def _stop_readers(self, stream: ActiveStream) -> None:
        for task in stream.readers:
            task.cancel()
        await asyncio.gather(*stream.readers, return_exceptions=True)

    async def _finish(self, stream: ActiveStream) -> None:
        await self._stop_readers(stream)
        ended = await stream.conn.end(self._end_timeout)
        stream.outbox.close()
        if ended:
            with contextlib.suppress(OSError):
                shutil.rmtree(stream.outbox.directory)
            log.info("stream %s: session %s ended", stream.name, stream.session_id)
        else:
            log.warning(
                "stream %s: session %s stopped without the server confirming the end; "
                "its outbox is kept at %s",
                stream.name,
                stream.session_id,
                stream.outbox.directory,
            )
