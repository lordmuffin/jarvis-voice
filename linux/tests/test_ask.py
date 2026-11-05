"""Hold to ask from the app's side, against a fake daemon on a real control socket.

The fake speaks the daemon's control commands as stage 1 built them (server/src/jarvis_live/
client/daemon.py): ``subscribe`` sends the voice state, then live events; ``ask_begin`` and
``ask_end`` answer with an id or an error."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from jarvis_live.client import control

from jarvis_desktop.ask import AskController, SocketControl, watch
from jarvis_desktop.presentation import AskPhase, AskView


class FakeDaemon:
    def __init__(self, path: Path, voice: bool = True) -> None:
        self.path = path
        self.voice = voice
        self.requests: list[dict[str, Any]] = []
        self.open: str | None = None
        self.refuse_begin: str | None = None
        self.hold_begin: asyncio.Event | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._n = 0
        self.server = control.ControlServer(path, self._handle)

    async def _handle(self, msg: dict[str, Any], send: control.Send) -> None:
        cmd = msg["cmd"]
        if cmd == "subscribe":
            q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
            self._subscribers.add(q)
            await send({"type": "segment", "text": "backlog, ignored"})
            await send({"type": "voice_state", "available": self.voice})
            try:
                while (event := await q.get()) is not None:
                    await send(event)
            finally:
                self._subscribers.discard(q)
            return
        self.requests.append(msg)
        if cmd == "ask_begin":
            if self.hold_begin is not None:
                await self.hold_begin.wait()
            if self.refuse_begin:
                await send({"error": self.refuse_begin})
            elif not self.voice:
                await send({"error": "voice is not available in this session"})
            elif self.open:
                await send({"error": f"an ask is already open ({self.open})"})
            else:
                self._n += 1
                self.open = f"ask{self._n}"
                await send({"ok": True, "id": self.open, "t_ms": 1000 * self._n})
        elif cmd == "ask_end":
            if self.open is None:
                await send({"error": "no ask is open"})
            else:
                ask_id, self.open = self.open, None
                await send({"ok": True, "id": ask_id, "t_ms": 1500})
        else:
            await send({"error": f"unknown command {cmd!r}"})

    def push(self, event: dict[str, Any]) -> None:
        for q in self._subscribers:
            q.put_nowait(event)

    def push_voice(self, available: bool) -> None:
        self.voice = available
        self.push({"type": "voice_state", "available": available})

    def cmds(self) -> list[str]:
        return [r["cmd"] for r in self.requests]


class App:
    """The controller, the views it showed and the watcher feeding it."""

    def __init__(self, path: Path, **timing: float) -> None:
        self.views: list[AskView] = []
        self.ask = AskController(SocketControl(path), self.views.append, **timing)
        self.watcher = asyncio.create_task(watch(path, self.ask, retry=0.02))

    @property
    def phase(self) -> AskPhase:
        return self.ask.view.phase

    async def until(self, phase: AskPhase, timeout: float = 2.0) -> None:
        async def wait() -> None:
            while self.phase is not phase:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait(), timeout)

    async def until_session(self, voice: bool) -> None:
        async def wait() -> None:
            while not (self.ask.session and self.ask.voice is voice):
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait(), 2.0)

    async def hold(self) -> None:
        await self.ask.down()

    async def release(self) -> None:
        await self.ask.up()


@pytest.fixture
def sock(tmp_path: Path) -> Path:
    return tmp_path / "ctl.sock"


@pytest.fixture
async def daemon(sock: Path) -> AsyncIterator[FakeDaemon]:
    d = FakeDaemon(sock)
    await d.server.start()
    yield d
    for q in list(d._subscribers):
        q.put_nowait(None)
    await d.server.close()


@pytest.fixture
async def app(sock: Path) -> AsyncIterator[App]:
    a = App(sock, reply_s=0.1, message_s=0.05, wait_timeout=0.5)
    yield a
    a.watcher.cancel()
    await asyncio.gather(a.watcher, return_exceptions=True)
    await a.ask.close()


async def test_hold_asks_and_shows_the_reply(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    await app.hold()
    assert app.phase is AskPhase.LISTENING
    assert daemon.cmds() == ["ask_begin"]
    await app.release()
    assert app.phase is AskPhase.WAITING
    assert daemon.cmds() == ["ask_begin", "ask_end"]
    daemon.push({"type": "ask_reply", "id": "ask1", "outcome": "answer", "text": "It's 3 pm."})
    await app.until(AskPhase.REPLY)
    assert app.ask.view == AskView(AskPhase.REPLY, "It's 3 pm.")
    await app.until(AskPhase.IDLE)
    phases = [v.phase for v in app.views]
    assert phases == [AskPhase.LISTENING, AskPhase.WAITING, AskPhase.REPLY, AskPhase.IDLE]


async def test_no_session_shows_why_and_sends_nothing(sock: Path, app: App) -> None:
    await app.hold()
    assert app.phase is AskPhase.NO_SESSION
    await app.release()
    assert app.phase is AskPhase.NO_SESSION, "release has nothing to end"
    await app.until(AskPhase.IDLE)
    assert not sock.exists()


async def test_voice_off_shows_why_and_sends_nothing(daemon: FakeDaemon, sock: Path) -> None:
    daemon.voice = False
    a = App(sock, message_s=0.05)
    try:
        await a.until_session(voice=False)
        await a.hold()
        assert a.phase is AskPhase.VOICE_OFF
        await a.release()
        assert daemon.requests == []
    finally:
        a.watcher.cancel()
        await asyncio.gather(a.watcher, return_exceptions=True)
        await a.ask.close()


async def test_voice_coming_on_later_allows_asks(daemon: FakeDaemon, sock: Path) -> None:
    daemon.voice = False
    a = App(sock)
    try:
        await a.until_session(voice=False)
        daemon.push_voice(True)
        await a.until_session(voice=True)
        await a.hold()
        assert a.phase is AskPhase.LISTENING
    finally:
        a.watcher.cancel()
        await asyncio.gather(a.watcher, return_exceptions=True)
        await a.ask.close()


async def test_daemon_refusing_voice_shows_voice_off(daemon: FakeDaemon, app: App) -> None:
    """Voice went off between the last voice_state and the press."""
    await app.until_session(voice=True)
    daemon.voice = False
    await app.hold()
    assert app.phase is AskPhase.VOICE_OFF
    await app.release()
    assert daemon.cmds() == ["ask_begin"], "no ask_end for an ask that never began"


async def test_local_session_refusal_shows_voice_off(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    daemon.refuse_begin = "voice is not available: no streaming session"
    await app.hold()
    assert app.phase is AskPhase.VOICE_OFF


async def test_other_errors_show_the_daemons_message(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    daemon.refuse_begin = "something broke"
    await app.hold()
    assert app.ask.view == AskView(AskPhase.FAILED, "something broke")


async def test_an_ask_left_open_is_closed_and_asked_again(daemon: FakeDaemon, app: App) -> None:
    """An app that died holding the key left the daemon's ask open."""
    await app.until_session(voice=True)
    daemon.open = "stale"
    await app.hold()
    assert app.phase is AskPhase.LISTENING
    assert daemon.cmds() == ["ask_begin", "ask_end", "ask_begin"]


async def test_release_during_a_slow_begin_ends_after_it(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    daemon.hold_begin = asyncio.Event()
    down = asyncio.create_task(app.hold())
    up = asyncio.create_task(app.release())
    await asyncio.sleep(0.05)
    assert daemon.cmds() == ["ask_begin"]
    daemon.hold_begin.set()
    await asyncio.gather(down, up)
    assert daemon.cmds() == ["ask_begin", "ask_end"]
    assert app.phase is AskPhase.WAITING


async def test_reply_to_another_ask_is_ignored(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    await app.hold()
    await app.release()
    daemon.push({"type": "ask_reply", "id": "old", "outcome": "answer", "text": "not yours"})
    await asyncio.sleep(0.05)
    assert app.phase is AskPhase.WAITING


async def test_empty_reply_is_nothing_heard(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    await app.hold()
    await app.release()
    daemon.push({"type": "ask_reply", "id": "ask1", "outcome": "empty", "text": ""})
    await app.until(AskPhase.NOTHING_HEARD)


async def test_failed_reply_without_text_is_no_answer(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    await app.hold()
    await app.release()
    daemon.push({"type": "ask_reply", "id": "ask1", "outcome": "timeout", "text": ""})
    await app.until(AskPhase.NO_ANSWER)


async def test_no_reply_in_time_is_no_answer(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    await app.hold()
    await app.release()
    await app.until(AskPhase.NO_ANSWER, timeout=2.0)
    await app.until(AskPhase.IDLE)


async def test_daemon_going_away_while_waiting_shows_no_session(
    daemon: FakeDaemon, app: App
) -> None:
    await app.until_session(voice=True)
    await app.hold()
    await app.release()
    for q in list(daemon._subscribers):
        q.put_nowait(None)
    await app.until(AskPhase.NO_SESSION)
    assert not app.ask.session


async def test_a_new_hold_replaces_a_shown_reply(daemon: FakeDaemon, app: App) -> None:
    await app.until_session(voice=True)
    await app.hold()
    await app.release()
    daemon.push({"type": "ask_reply", "id": "ask1", "outcome": "answer", "text": "One."})
    await app.until(AskPhase.REPLY)
    await app.hold()
    assert app.phase is AskPhase.LISTENING
    await asyncio.sleep(0.15)
    assert app.phase is AskPhase.LISTENING, "the old reply's dismiss doesn't hide the new ask"


def test_socket_path_is_the_capture_daemons(tmp_path: Path) -> None:
    from jarvis_desktop.ask import socket_path

    env = {"XDG_RUNTIME_DIR": str(tmp_path)}
    assert socket_path(env) == tmp_path / "jarvis-live" / "ctl.sock"
