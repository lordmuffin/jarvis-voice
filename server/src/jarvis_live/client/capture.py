"""Microphone / system-audio capture by shelling out to the sound server's own recorder.

``parec`` (PulseAudio and PipeWire's pipewire-pulse) or ``pw-record`` resample and downmix to the
wire format (16 kHz mono s16le), so the client needs no audio library and no GUI. Raw PCM from the
recorder's stdout is cut into exact frames and stored in the outbox.
"""

import asyncio
import collections
import contextlib
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from jarvis_live.client.outbox import Outbox, StoredFrame
from jarvis_live.protocol import BYTES_PER_MS, MIN_PAYLOAD

FRAME_MS = 100
HEALTHY_MS = 1_000  # a recorder that produced this much audio before dying was not "rapid failing"


class CaptureError(Exception):
    pass


def find_tool() -> str:
    for tool in ("parec", "pw-record"):
        if shutil.which(tool):
            return tool
    raise CaptureError(
        "no audio recorder found: install pulseaudio-utils (parec) or pipewire-bin (pw-record)"
    )


def build_command(tool: str, source: str | None) -> list[str]:
    if tool == "parec":
        cmd = ["parec", "--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=20"]
        return cmd + ([f"--device={source}"] if source else [])
    if tool == "pw-record":
        cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
        return cmd + (["--target", source] if source else []) + ["-"]
    raise CaptureError(f"unsupported recorder {tool!r}")


async def default_monitor() -> str:
    """The monitor source of the default sink: what is being played, for the system channel."""
    if shutil.which("pactl") is None:
        raise CaptureError("pactl not found: it is needed to locate the system-audio monitor")
    proc = await asyncio.create_subprocess_exec(
        "pactl",
        "get-default-sink",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    sink = out.decode().strip()
    if proc.returncode != 0 or not sink:
        raise CaptureError(f"cannot find the default sink: {err.decode().strip() or 'none set'}")
    return f"{sink}.monitor"


class FrameCutter:
    """Cuts a byte stream into exact ``frame_ms`` PCM frames."""

    def __init__(self, frame_ms: int = FRAME_MS) -> None:
        self._size = frame_ms * BYTES_PER_MS
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        n = len(self._buf) // self._size
        frames = [bytes(self._buf[i * self._size : (i + 1) * self._size]) for i in range(n)]
        del self._buf[: n * self._size]
        return frames

    def flush(self) -> bytes | None:
        """The buffered tail, zero-padded up to the 20 ms protocol minimum; None if empty."""
        if not self._buf:
            return None
        tail = bytes(self._buf).ljust(MIN_PAYLOAD, b"\x00")
        self._buf.clear()
        return tail


@dataclass(frozen=True)
class RestartPolicy:
    initial_delay: float = 0.5
    max_delay: float = 10.0
    max_rapid: int = 5  # consecutive quick failures before giving up

    def delay(self, attempt: int) -> float:
        return min(self.max_delay, self.initial_delay * 2.0 ** min(attempt, 30))


class Capturer:
    """Runs a recorder, storing its audio as frames in the outbox, restarting it if it dies.

    Seqs stay contiguous across restarts (the server rejects gaps). ``t_ms`` comes from samples
    emitted, so it cannot drift; after a restart it jumps forward by the wall-clock gap so the
    transcript timeline stays honest."""

    def __init__(
        self,
        argv: list[str],
        channel: str,
        outbox: Outbox,
        *,
        frame_ms: int = FRAME_MS,
        start_ms: int = 0,
        clock: Callable[[], float] = time.monotonic,
        restart: RestartPolicy = RestartPolicy(),  # noqa: B008
        on_frame: Callable[[StoredFrame], None] | None = None,
    ) -> None:
        self.argv = argv
        self.channel = channel
        self.outbox = outbox
        self.frame_ms = frame_ms
        self._start_ms = start_ms
        self._clock = clock
        self._restart = restart
        self._on_frame = on_frame
        self.restarts = 0
        self.last_error: str | None = None
        self.last_frame_at: float | None = None  # time.monotonic() of the newest stored frame

    async def run(self) -> None:
        t0 = self._clock() - self._start_ms / 1000
        timeline_end = self._start_ms
        rapid = 0
        first = True
        while True:
            base = self._start_ms if first else max(timeline_end, int((self._clock() - t0) * 1000))
            first = False
            started = time.monotonic()
            produced_ms, stderr = 0, ""
            try:
                produced_ms, stderr = await self._run_process(base)
            except OSError as e:
                raise CaptureError(f"cannot start {self.argv[0]}: {e}") from e
            timeline_end = max(timeline_end, base + produced_ms)
            quick = produced_ms < HEALTHY_MS and time.monotonic() - started < 5
            rapid = rapid + 1 if quick else 0
            self.last_error = stderr or f"{self.argv[0]} exited"
            if rapid >= self._restart.max_rapid:
                raise CaptureError(f"audio source keeps failing: {self.last_error}")
            await asyncio.sleep(self._restart.delay(max(rapid - 1, 0)))
            self.restarts += 1

    async def _run_process(self, base_ms: int) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None
        err_lines: collections.deque[str] = collections.deque(maxlen=5)

        async def drain_stderr() -> None:
            assert proc.stderr is not None
            async for line in proc.stderr:
                err_lines.append(line.decode(errors="replace").strip())

        err_task = asyncio.create_task(drain_stderr())
        cutter = FrameCutter(self.frame_ms)
        produced = 0
        try:
            while data := await proc.stdout.read(65536):
                for pcm in cutter.feed(data):
                    produced += self._store(pcm, base_ms + produced)
            if tail := cutter.flush():
                produced += self._store(tail, base_ms + produced)
            await proc.wait()
            await err_task
        finally:
            err_task.cancel()
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            # A cancel can interrupt the read loop before EOF; drain so the transport closes itself.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.stdout.read(), 1)
            await asyncio.gather(err_task, return_exceptions=True)
        return produced, " | ".join(x for x in err_lines if x)

    def _store(self, pcm: bytes, t_ms: int) -> int:
        frame = self.outbox.append(self.channel, pcm, t_ms=t_ms)
        self.last_frame_at = time.monotonic()
        if self._on_frame:
            self._on_frame(frame)
        return frame.duration_ms
