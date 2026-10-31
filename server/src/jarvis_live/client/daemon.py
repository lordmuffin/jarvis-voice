"""The long-running capture process: recorder(s) -> outbox -> transport, plus a control socket.

One daemon per user. In ``stream`` mode the transport is a ``ProducerConnection``; in ``local``
mode it is a ``NullTransport`` and nothing leaves the machine."""

import asyncio
import collections
import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_live.client import capture, control
from jarvis_live.client.config import ClientConfig, runtime_dir, state_dir
from jarvis_live.client.local import NullTransport
from jarvis_live.client.outbox import CHANNELS, Outbox, StoredFrame
from jarvis_live.client.transport import ProducerConnection, create_session, session_create_body
from jarvis_live.protocol import ServerMessage

log = logging.getLogger("jarvis_live.client")
Recorder = Callable[[str], Awaitable[list[str]]]
HISTORY = 500
LOCAL_NOTE = "nothing leaves this machine"


class OutboxFull(capture.CaptureError):
    pass


@dataclass
class SessionOptions:
    title: str | None = None
    meeting: bool = False
    system: bool = False
    resume: bool = False  # continue the latest unfinished session of the same mode, if any
    roll: bool = False  # start a new session after max_session_s (standing capture endpoints)


def find_resumable(state: Path, mode: str | None = None) -> str | None:
    """The newest session that was not ended cleanly (and, if given, recorded in ``mode``)."""
    best: tuple[float, str] | None = None
    for meta_file in (state / "sessions").glob("*/session.json"):
        try:
            meta = json.loads(meta_file.read_text())
        except ValueError:
            continue
        if meta.get("ended") or (mode and meta.get("mode") != mode):
            continue
        if best is None or meta["started_at"] > best[0]:
            best = (meta["started_at"], meta["id"])
    return best[1] if best else None


def _write_meta(directory: Path, meta: dict[str, Any]) -> None:
    tmp = directory / "session.json.tmp"
    tmp.write_text(json.dumps(meta))
    tmp.replace(directory / "session.json")


async def default_recorder(source: str | None, system: bool) -> Recorder:
    tool = capture.find_tool()

    async def recorder(channel: str) -> list[str]:
        if channel == "system":
            return capture.build_command(tool, await capture.default_monitor())
        return capture.build_command(tool, source)

    return recorder


class _Run:
    """Everything about the session currently being recorded."""

    def __init__(self, meta: dict[str, Any], outbox: Outbox, sink: Any) -> None:
        self.meta = meta
        self.outbox = outbox
        self.sink = sink
        self.state = "recording"
        self.error: str | None = None
        self.restarts = 0
        self.history: collections.deque[dict[str, Any]] = collections.deque(maxlen=HISTORY)
        self.subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()


class Daemon:
    def __init__(
        self,
        cfg: ClientConfig,
        env: Mapping[str, str],
        opts: SessionOptions,
        *,
        recorder: Recorder | None = None,
        end_timeout: float = 30.0,
        max_outbox_bytes: int | None = None,
        max_session_s: float | None = None,
        restart: capture.RestartPolicy = capture.RestartPolicy(),  # noqa: B008
    ) -> None:
        self.cfg = cfg
        self.env = env
        self.opts = opts
        self._recorder = recorder
        self.end_timeout = end_timeout
        self._restart = restart
        self.exit_code = 0
        self.max_outbox_bytes = max_outbox_bytes or cfg.max_outbox_mb * 1024 * 1024
        self.max_session_s = max_session_s or cfg.max_session_hours * 3600
        self._stop = asyncio.Event()
        self._finished = asyncio.Event()
        self._stop_result: dict[str, Any] = {"ok": True, "ended": False, "final_note": None}
        self._run: _Run | None = None
        self._run_ready = asyncio.Event()
        self._control: control.ControlServer | None = None
        self._run_dir = runtime_dir(env)
        self._state_dir = state_dir(env)

    # --- lifecycle ------------------------------------------------------------------------

    async def run(self) -> int:
        self.cfg.validate()
        await self._claim()
        self._control = control.ControlServer(self._run_dir / "ctl.sock", self._handle)
        try:
            await self._control.start()
            (self._run_dir / "daemon.pid").write_text(str(os.getpid()))
            resume = self.opts.resume
            while True:
                rolled = await self._session(resume)
                resume = False
                if not rolled:
                    return self.exit_code
        finally:
            self._finished.set()
            if self._control is not None:
                await self._control.close()
            with contextlib.suppress(FileNotFoundError):
                (self._run_dir / "daemon.pid").unlink()

    async def _claim(self) -> None:
        self._run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._run_dir.chmod(0o700)
        sock = self._run_dir / "ctl.sock"
        if sock.exists():
            try:
                st = await control.request(sock, {"cmd": "status"}, timeout=1.0)
            except (OSError, control.ControlError):
                sock.unlink()  # stale: left behind by a crash
            else:
                raise RuntimeError(
                    f"already running (pid {st.get('pid')}, session {st.get('session')})"
                )
        with contextlib.suppress(FileNotFoundError):
            (self._run_dir / "daemon.pid").unlink()

    def request_stop(self) -> None:
        self._stop.set()

    # --- one session ----------------------------------------------------------------------

    async def _open(self, resume: bool) -> tuple[dict[str, Any], Outbox, int]:
        sessions = self._state_dir / "sessions"
        sid = find_resumable(self._state_dir, self.cfg.mode) if resume else None
        if sid is not None:
            outbox = Outbox.reopen(sid, sessions)
            meta = json.loads((outbox.directory / "session.json").read_text())
            last_end = max(
                (f.t_ms + f.duration_ms for c in CHANNELS for f in outbox.unacked(c)[-1:]),
                default=0,
            )
            wall_ms = int((time.time() - meta["started_at"]) * 1000)
            return meta, outbox, max(last_end, wall_ms)
        title = self.opts.title
        if self.cfg.mode == "stream":
            assert self.cfg.server and self.cfg.token
            body = session_create_body(title, meeting=self.opts.meeting, system=self.opts.system)
            sid = await create_session(self.cfg.server, self.cfg.token, body)
        else:
            sid = str(uuid.uuid4())
        outbox = Outbox(sid, sessions)
        meta = {
            "id": sid,
            "mode": self.cfg.mode,
            "title": title,
            "started_at": time.time(),
            "server": self.cfg.server if self.cfg.mode == "stream" else None,
            "system": self.opts.system,
            "ended": False,
        }
        _write_meta(outbox.directory, meta)
        return meta, outbox, 0

    async def _session(self, resume: bool) -> bool:
        """Record one session. Returns True if it rolled over and another should start."""
        meta, outbox, start_ms = await self._open(resume)
        sink: Any
        if self.cfg.mode == "stream":
            assert self.cfg.server and self.cfg.token
            sink = ProducerConnection(
                self.cfg.server, self.cfg.token, meta["id"], outbox, device=self.cfg.device
            )
        else:
            sink = NullTransport(outbox)
        run = self._run = _Run(meta, outbox, sink)
        self._run_ready.set()
        mode_label = "LOCAL ONLY" if self.cfg.mode == "local" else "STREAMING"
        log.info("session %s: %s%s", meta["id"], mode_label, " (resumed)" if start_ms else "")

        recorder = self._recorder or await default_recorder(self.cfg.source, self.opts.system)
        channels = ["mic", "system"] if meta.get("system") else ["mic"]

        def on_frame(frame: StoredFrame) -> None:
            sink.kick()
            if outbox.unacked_bytes() > self.max_outbox_bytes:
                raise OutboxFull(
                    f"outbox is full ({self.max_outbox_bytes // 1024} KiB unacked): "
                    "capture stopped to protect the disk"
                )

        capturers = [
            capture.Capturer(
                await recorder(c),
                c,
                outbox,
                start_ms=start_ms,
                on_frame=on_frame,
                restart=self._restart,
            )
            for c in channels
        ]
        cap_tasks = [asyncio.create_task(c.run()) for c in capturers]
        sink.start()
        pump = asyncio.create_task(self._pump(run))
        stop_wait = asyncio.create_task(self._stop.wait())
        closed = asyncio.create_task(sink.wait_closed())
        roll = asyncio.create_task(asyncio.sleep(self.max_session_s)) if self.opts.roll else None
        rolled, cleanly_ended = False, False
        try:
            watched = {stop_wait, closed, *cap_tasks} | ({roll} if roll else set())
            while True:
                done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                if stop_wait in done:
                    break
                if roll is not None and roll in done:
                    rolled = True
                    break
                if closed in done:  # the transport gave up (e.g. credentials rejected)
                    run.state, run.error = "failed", sink.failure or "transport closed"
                    watched.discard(closed)
                for t in done & set(cap_tasks):
                    watched.discard(t)
                    exc = t.exception()
                    run.state, run.error = "failed", str(exc) if exc else "recorder exited"
                if run.state == "failed" and self.opts.roll:
                    self.exit_code = 1  # standing endpoint: exit so the service manager retries
                    break
                if run.state == "failed" and stop_wait not in done:
                    for t in cap_tasks:
                        t.cancel()
                    watched = {stop_wait} | ({roll} if roll else set())
            run.state = "stopping" if not rolled else "rolling"
            for t in cap_tasks:
                t.cancel()
            await asyncio.gather(*cap_tasks, return_exceptions=True)
            run.restarts = sum(c.restarts for c in capturers)
            cleanly_ended = await sink.end(self.end_timeout)
        finally:
            for t in (stop_wait, closed, pump, *cap_tasks, *([roll] if roll else [])):
                t.cancel()
            await asyncio.gather(
                stop_wait,
                closed,
                pump,
                *cap_tasks,
                *([roll] if roll else []),
                return_exceptions=True,
            )
            await sink.stop()
            for q in run.subscribers:
                q.put_nowait(None)
            outbox.close()
        if cleanly_ended:
            meta["ended"] = True
            _write_meta(outbox.directory, meta)
        self._stop_result = {
            "ok": True,
            "ended": cleanly_ended,
            "session": meta["id"],
            "final_note": next(
                (e for e in reversed(run.history) if e.get("type") == "final_note"), None
            ),
        }
        if not rolled:
            self._finished.set()
        return rolled

    async def _pump(self, run: _Run) -> None:
        """Relay server events to history and subscribers."""
        while True:
            msg: ServerMessage = await run.sink.events.get()
            event = json.loads(msg.model_dump_json())
            run.history.append(event)
            for q in run.subscribers:
                q.put_nowait(event)

    # --- control --------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        run = self._run
        local = self.cfg.mode == "local"
        base: dict[str, Any] = {
            "pid": os.getpid(),
            "mode": "LOCAL ONLY" if local else "STREAMING",
            "mode_note": LOCAL_NOTE if local else None,
            "device": self.cfg.device,
            "source": self.cfg.source,
        }
        if run is None:
            return {**base, "state": "starting", "session": None}
        sid = run.meta["id"]
        unacked = sum(run.outbox.unacked_duration_ms(c) for c in CHANNELS)
        return {
            **base,
            "state": run.state,
            "problem": run.error,
            "session": sid,
            "title": run.meta.get("title"),
            "started_at": run.meta["started_at"],
            "uptime_s": int(time.time() - run.meta["started_at"]),
            "connection": run.sink.state,
            "unacked_ms": unacked,
            "acked": run.outbox.acked_seqs(),
            "dashboard_url": None if local else f"{self.cfg.server}/sessions/{sid}",
            "restarts": run.restarts,
        }

    async def _handle(self, msg: dict[str, Any], send: control.Send) -> None:
        cmd = msg.get("cmd")
        if cmd == "status":
            await send(self.status())
        elif cmd == "stop":
            self.request_stop()
            await self._finished.wait()
            await send(self._stop_result)
        elif cmd == "subscribe":
            await self._subscribe(send)
        else:
            await send({"error": f"unknown command {cmd!r}"})

    async def _subscribe(self, send: control.Send) -> None:
        await self._run_ready.wait()
        run = self._run
        assert run is not None
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        for past in list(run.history):  # backlog first, then live
            await send(past)
        run.subscribers.add(q)
        try:
            while (event := await q.get()) is not None:
                await send(event)
        finally:
            run.subscribers.discard(q)
