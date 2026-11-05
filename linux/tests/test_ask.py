"""Hold to ask from the app's side, against a fake daemon on a real control socket.

The fake speaks the daemon's control commands as stage 1 built them (server/src/jarvis_live/
client/daemon.py): ``subscribe`` sends the voice state, then live events; ``ask_begin`` and
``ask_end`` answer with an id or an error."""

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from jarvis_live.client import control

from jarvis_desktop.ask import AskController, SocketControl, watch
from jarvis_desktop.presentation import AskPhase, AskView, NotSpoken
from jarvis_desktop.route import Route, Sink
from jarvis_desktop.speech import SpeechError


class FakeDaemon:
    def __init__(self, path: Path, voice: bool = True) -> None:
        self.path = path
        self.voice = voice
        self.requests: list[dict[str, Any]] = []
        self.open: str | None = None
        self.refuse_begin: str | None = None
        self.hold_begin: asyncio.Event | None = None
        self.system: bool | None = False  # None: an older daemon whose status doesn't say
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
        elif cmd == "status":
            st: dict[str, Any] = {"state": "recording", "voice": self.voice}
            if self.system is not None:
                st["system"] = self.system
            await send(st)
        elif cmd == "ask_played":
            await send({"ok": True})
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

    def played(self) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in r.items() if k != "cmd"}
            for r in self.requests
            if r["cmd"] == "ask_played"
        ]


class App:
    """The controller, the views it showed and the watcher feeding it."""

    def __init__(self, path: Path, **kw: Any) -> None:
        self.views: list[AskView] = []
        self.ask = AskController(SocketControl(path), self.views.append, **kw)
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


# --- spoken replies, headphones only -------------------------------------------------------

JACK = Sink("alsa_output.jack", "alsa", "pci", None, "analog-stereo", "headphones", "yes")
BUILT_IN = Sink("alsa_output.jack", "alsa", "pci", None, "analog-stereo", "speaker", "yes")


class FakeSpeaker:
    def __init__(self, *, delay: float = 0.05, length: float = 0.05) -> None:
        self.delay = delay  # synthesis, before the first audio
        self.length = length
        self.fail = False
        self.fail_warm = False
        self.warmed = 0
        self.spoken: list[tuple[str, str]] = []
        self.stopped = False

    async def warm(self) -> None:
        self.warmed += 1
        if self.fail_warm:
            raise SpeechError("no voice")

    async def speak(self, text: str, sink: str, on_first_audio: Callable[[], None]) -> None:
        self.spoken.append((text, sink))
        try:
            await asyncio.sleep(self.delay)
            if self.fail:
                raise SpeechError("broke")
            on_first_audio()
            await asyncio.sleep(self.length)
        except asyncio.CancelledError:
            self.stopped = True
            raise


class Routes:
    def __init__(self, route: Route = Route.HEADPHONES, sink: Sink | None = JACK) -> None:
        self.now: tuple[Route, Sink | None] = (route, sink)

    async def __call__(self) -> tuple[Route, Sink | None]:
        return self.now


@pytest.fixture
def speaker() -> FakeSpeaker:
    return FakeSpeaker()


@pytest.fixture
def routes() -> Routes:
    return Routes()


@pytest.fixture
async def talker(sock: Path, speaker: FakeSpeaker, routes: Routes) -> AsyncIterator[App]:
    a = App(
        sock,
        speaker=speaker,
        read_route=routes,
        poll=0.01,
        reply_s=0.1,
        message_s=0.05,
        wait_timeout=0.5,
    )
    yield a
    a.watcher.cancel()
    await asyncio.gather(a.watcher, return_exceptions=True)
    await a.ask.close()


async def asked(daemon: FakeDaemon, app: App, text: str = "It's 3 pm.", **reply: str) -> None:
    await app.until_session(voice=True)
    await app.hold()
    await app.release()
    daemon.push({"type": "ask_reply", "id": "ask1", "outcome": "answer", "text": text, **reply})


async def eventually(cond: Callable[[], bool], timeout: float = 2.0) -> None:
    async def wait() -> None:
        while not cond():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), timeout)


async def test_the_voice_warms_when_a_session_starts(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    await talker.until_session(voice=True)
    await eventually(lambda: speaker.warmed == 1)
    daemon.push_voice(False)
    daemon.push_voice(True)
    await asyncio.sleep(0.05)
    assert speaker.warmed == 1, "once per session, not per voice_state"


async def test_a_voice_that_fails_to_warm_does_not_stop_asks(
    daemon: FakeDaemon, sock: Path, routes: Routes
) -> None:
    spk = FakeSpeaker()
    spk.fail_warm = True
    a = App(sock, speaker=spk, read_route=routes, poll=0.01)
    try:
        await a.until_session(voice=True)
        await eventually(lambda: spk.warmed == 1)
        await a.hold()
        assert a.phase is AskPhase.LISTENING
    finally:
        a.watcher.cancel()
        await asyncio.gather(a.watcher, return_exceptions=True)
        await a.ask.close()


async def test_headphones_speak_and_report_started_then_done(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    await asked(daemon, talker)
    await talker.until(AskPhase.SPEAKING)
    assert talker.ask.view == AskView(AskPhase.SPEAKING, "It's 3 pm.")
    await talker.until(AskPhase.REPLY)
    assert talker.ask.view == AskView(AskPhase.REPLY, "It's 3 pm.")
    assert speaker.spoken == [("It's 3 pm.", "alsa_output.jack")]
    await eventually(lambda: len(daemon.played()) == 2)
    started, done = daemon.played()
    assert started["id"] == "ask1" and started["state"] == "started"
    assert 50 <= started["latency_ms"] < 1000, "key up to first audio, synthesis included"
    assert done == {"id": "ask1", "state": "done"}
    assert daemon.cmds() == ["ask_begin", "ask_end", "status", "ask_played", "ask_played"]


async def test_no_headphones_shows_the_text_and_reports_skipped(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker, routes: Routes
) -> None:
    routes.now = (Route.SPEAKERS, BUILT_IN)
    await asked(daemon, talker)
    await talker.until(AskPhase.REPLY)
    assert talker.ask.view == AskView(AskPhase.REPLY, "It's 3 pm.", NotSpoken.ROUTE)
    await eventually(lambda: len(daemon.played()) == 1)
    assert daemon.played() == [{"id": "ask1", "state": "skipped", "reason": "route"}]
    assert speaker.spoken == []
    assert AskPhase.SPEAKING not in [v.phase for v in talker.views]


async def test_an_unknown_route_is_text_only(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker, routes: Routes
) -> None:
    routes.now = (Route.UNKNOWN, None)
    await asked(daemon, talker)
    await talker.until(AskPhase.REPLY)
    assert talker.ask.view.not_spoken is NotSpoken.ROUTE
    assert speaker.spoken == []


@pytest.mark.parametrize("system", [True, None], ids=["--system", "status does not say"])
async def test_a_session_recording_what_this_box_plays_is_text_only(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker, system: bool | None
) -> None:
    """The system channel is the default sink's monitor: it would record Jarvis's reply."""
    daemon.system = system
    await asked(daemon, talker)
    await talker.until(AskPhase.REPLY)
    assert talker.ask.view == AskView(AskPhase.REPLY, "It's 3 pm.", NotSpoken.TRANSCRIPT)
    await eventually(lambda: len(daemon.played()) == 1)
    assert daemon.played() == [{"id": "ask1", "state": "skipped", "reason": "route"}]
    assert speaker.spoken == []


async def test_unplugging_while_speaking_stops_at_once(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker, routes: Routes
) -> None:
    speaker.length = 30
    await asked(daemon, talker)
    await talker.until(AskPhase.SPEAKING)
    await eventually(lambda: len(daemon.played()) == 1)
    routes.now = (Route.SPEAKERS, BUILT_IN)
    await talker.until(AskPhase.REPLY, timeout=0.5)
    assert speaker.stopped
    assert talker.ask.view == AskView(AskPhase.REPLY, "It's 3 pm.", NotSpoken.INTERRUPTED)
    await eventually(lambda: len(daemon.played()) == 2)
    assert daemon.played()[1] == {"id": "ask1", "state": "skipped", "reason": "interrupted"}


async def test_speech_that_fails_is_text_only_and_says_so(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    speaker.fail = True
    await asked(daemon, talker)
    await talker.until(AskPhase.REPLY)
    assert talker.ask.view == AskView(AskPhase.REPLY, "It's 3 pm.", NotSpoken.SPEECH_FAILED)
    await eventually(lambda: len(daemon.played()) == 1)
    assert daemon.played() == [{"id": "ask1", "state": "skipped", "reason": "tts_failed"}]


async def test_an_empty_reply_speaks_nothing(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    await asked(daemon, talker, text="", outcome="empty")
    await talker.until(AskPhase.NOTHING_HEARD)
    await asyncio.sleep(0.05)
    assert speaker.spoken == [] and daemon.played() == []


async def test_a_failed_reply_speaks_nothing(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    await asked(daemon, talker, text="Sorry, something went wrong.", outcome="failed")
    await talker.until(AskPhase.REPLY)
    assert talker.ask.view == AskView(AskPhase.REPLY, "Sorry, something went wrong.")
    await asyncio.sleep(0.05)
    assert speaker.spoken == [] and daemon.played() == []


async def test_a_filed_reply_is_spoken(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    await asked(daemon, talker, text="Filed.", outcome="filed")
    await talker.until(AskPhase.SPEAKING)
    assert speaker.spoken == [("Filed.", "alsa_output.jack")]


async def test_a_new_hold_while_speaking_stops_it(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    speaker.length = 30
    await asked(daemon, talker)
    await talker.until(AskPhase.SPEAKING)
    await eventually(lambda: len(daemon.played()) == 1)
    await talker.hold()
    assert talker.phase is AskPhase.LISTENING
    assert speaker.stopped
    await eventually(lambda: len(daemon.played()) == 2)
    assert daemon.played()[1] == {"id": "ask1", "state": "skipped", "reason": "superseded"}


async def test_the_daemon_going_while_speaking_stops_it(
    daemon: FakeDaemon, talker: App, speaker: FakeSpeaker
) -> None:
    speaker.length = 30
    await asked(daemon, talker)
    await talker.until(AskPhase.SPEAKING)
    talker.ask.on_session_ended()
    await eventually(lambda: speaker.stopped)
    assert talker.phase is AskPhase.NO_SESSION
