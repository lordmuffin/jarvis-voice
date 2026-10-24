"""Recorded files uploaded after the fact: decoded to the stream codec, then fed through the same
ingest path as a live producer (store, VAD, STT, copilot, finalizer)."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from jarvis_live.ingest.hub import IngestHub, SessionIngest
from jarvis_live.logging_setup import session_logger
from jarvis_live.processing import fraction
from jarvis_live.protocol import BYTES_PER_MS, CHANNEL_MIC, MAX_PAYLOAD, Frame

log = logging.getLogger(__name__)

# Feeding pauses while this many segments wait for STT: a file arrives far faster than it can
# be transcribed, and the queue would otherwise hold most of a long recording in memory.
UPLOAD_MAX_BACKLOG = 8
FRAME_MS = MAX_PAYLOAD // BYTES_PER_MS  # the largest frame a producer may send
# Containers phones and recorders actually produce. Playlist and concat demuxers (hls, concat,
# ...) are left out, so an uploaded file can never make ffmpeg open another file or a URL.
DEMUXERS = "wav,w64,mp3,mov,ogg,matroska,flac,aac,amr,caf,aiff,asf"


class UndecodableAudio(Exception):
    """ffmpeg found no audio it could read in the file."""


async def decode_to_pcm(src: Path, dst: Path, *, ffmpeg: str = "ffmpeg") -> None:
    """Decode any audio (or video's audio) to 16 kHz mono PCM16, the protocol codec."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        *("-nostdin", "-hide_banner", "-loglevel", "error"),
        *("-protocol_whitelist", "file", "-format_whitelist", DEMUXERS),
        *("-i", str(src), "-vn", "-ac", "1", "-ar", "16000"),
        *("-f", "s16le", "-acodec", "pcm_s16le", "-y", str(dst)),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
        raise UndecodableAudio(err.decode(errors="replace").strip()[-500:] or "no audio")


class Uploads:
    """Background ingestion of decoded uploads. The session is ``live`` while its audio is
    transcribed, so viewers watch the transcript arrive; then it ends like any other."""

    def __init__(self, hub: IngestHub) -> None:
        self._hub = hub
        self._tasks: set[asyncio.Task[None]] = set()
        self._since: dict[uuid.UUID, datetime] = {}
        self._feeding: dict[uuid.UUID, tuple[SessionIngest, int]] = {}  # with its length (ms)

    def start(self, session_id: uuid.UUID, pcm: Path) -> None:
        self._since[session_id] = datetime.now(UTC)
        task = asyncio.create_task(self._run(session_id, pcm))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def since(self, session_id: uuid.UUID) -> datetime | None:
        """When this upload's ingest began, until the session has been ended."""
        return self._since.get(session_id)

    def progress(self, session_id: uuid.UUID) -> float | None:
        """How much of this upload's recording is transcribed (0-1), while it is ingested."""
        if (feeding := self._feeding.get(session_id)) is None:
            return None
        ing, total_ms = feeding
        return fraction(ing.pipeline.transcribed_ms(), total_ms)

    async def _run(self, session_id: uuid.UUID, pcm: Path) -> None:
        try:
            await self._ingest(session_id, pcm)
        finally:
            self._since.pop(session_id, None)
            self._feeding.pop(session_id, None)

    async def _ingest(self, session_id: uuid.UUID, pcm: Path) -> None:
        slog = session_logger(__name__, session_id)
        ing: SessionIngest | None = None
        try:
            ing = await self._hub.get(session_id, ["mic"])
            ing.uploading = True
            self._feeding[session_id] = ing, ing.pipeline.ms_of(pcm.stat().st_size)
            seq = 0
            with pcm.open("rb") as f:
                while chunk := f.read(MAX_PAYLOAD):
                    while ing.pipeline.pending_segments() >= UPLOAD_MAX_BACKLOG:
                        await asyncio.sleep(0.05)
                    frame = Frame(CHANNEL_MIC, seq, seq * FRAME_MS, chunk)
                    if await ing.store.append(frame) == "stored":
                        ing.pipeline.feed("mic", chunk)
                    seq += 1
            slog.info("upload stored", extra={"frames": seq})
            await ing.pipeline.finish()  # transcribe it all while the session is still live
        except asyncio.CancelledError:
            raise  # shutdown: stays live; the idle watchdog ends it with what was stored
        except Exception:
            slog.exception("upload ingest failed; ending with what was stored")
        finally:
            if ing is not None:
                ing.uploading = False
            pcm.unlink(missing_ok=True)
        await self._hub.end(session_id)

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
