"""Hold to ask from the desktop app (docs/plans/voice-push-to-talk.md). No Qt, no evdev.

The app does not talk to the server: the capture daemon does. ``AskController`` turns the ask
key's ``ASK_BEGIN`` and ``ASK_END`` (trigger.py) into the daemon's ``ask_begin`` and ``ask_end``
control commands, and ``watch`` feeds it the daemon's ``subscribe`` stream (``voice_state`` and
``ask_reply``). It asks only while a capture session is running with voice available, and
otherwise shows why and sends nothing. What it shows is an ``AskView`` for the pill.

With a speaker, a reply is spoken only through headphones (route.py) and only when the session
does not also record what this box plays (``--system``): that channel is the default sink's
monitor, so it would carry Jarvis's voice into the transcript. Either way it reports to the
server, through the daemon's ``ask_played``, what it did: ``started`` (with ``latency_ms`` from
key up to the first audio) then ``done``, or ``skipped`` with the reason."""

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from jarvis_live.client import control
from jarvis_live.client.config import runtime_dir

from jarvis_desktop.presentation import AskPhase, AskView, NotSpoken
from jarvis_desktop.route import read_route as pw_read_route
from jarvis_desktop.speech import POLL_SECONDS, Played, ReadRoute, Speaker, speak_guarded

#: After release, how long to wait for the reply before saying there was none.
WAIT_TIMEOUT = 30.0
#: Outcomes whose text is Jarvis talking. A ``failed`` reply's apology is shown, not spoken.
SPOKEN_OUTCOMES = frozenset({"answer", "filed"})

log = logging.getLogger(__name__)


class Control(Protocol):
    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The daemon's reply; raises ``ControlError`` for its errors, ``OSError`` if it's gone."""
        ...


class SocketControl:
    """The capture daemon's control socket."""

    def __init__(self, path: Path, timeout: float = 2.0) -> None:
        self.path = path
        self.timeout = timeout

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await control.request(self.path, payload, timeout=self.timeout)


def socket_path(env: Mapping[str, str] = os.environ) -> Path:
    """Where ``jarvis-live capture`` puts its control socket."""
    return runtime_dir(env) / "ctl.sock"


class AskController:
    def __init__(
        self,
        control: Control,
        on_change: Callable[[AskView], None],
        *,
        wait_timeout: float = WAIT_TIMEOUT,
        reply_s: float | None = None,
        message_s: float | None = None,
        speaker: Speaker | None = None,
        read_route: ReadRoute | None = None,
        poll: float = POLL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._control = control
        self._on_change = on_change
        self._wait_timeout = wait_timeout
        self._reply_s = reply_s
        self._message_s = message_s
        #: A capture session is running: the daemon's subscription has sent its voice state.
        self.session = False
        #: The session's latest voice state: does the server take asks?
        self.voice = False
        self.view = AskView(AskPhase.IDLE)
        self._begin: asyncio.Task[str | None] | None = None
        self._id: str | None = None  # the ask whose reply the pill is waiting for
        self._timer: asyncio.Task[None] | None = None
        #: None: text only, nothing reported (no speech set up).
        self._speaker = speaker
        self._read_route = read_route or pw_read_route
        self._poll = poll
        self._clock = clock
        self._released = 0.0  # when ask_end went, for latency_ms
        self._warming: asyncio.Task[None] | None = None
        self._delivery: asyncio.Task[None] | None = None
        self._speaking: str | None = None  # the ask whose reply is playing
        self._reports: asyncio.Task[None] | None = None  # the last ask_played, in order

    # --- the key -----------------------------------------------------------------------------

    async def down(self) -> None:
        """``ASK_BEGIN``: the key has been held past the threshold."""
        self._id = None
        if self._speaking is not None:
            self._report(self._speaking, "skipped", reason="superseded")
        await self._stop_delivery()
        if not self.session:
            self._show(AskView(AskPhase.NO_SESSION))
            return
        if not self.voice:
            self._show(AskView(AskPhase.VOICE_OFF))
            return
        self._show(AskView(AskPhase.LISTENING))
        self._begin = asyncio.create_task(self._ask_begin())
        await asyncio.shield(self._begin)

    async def up(self) -> None:
        """``ASK_END``: the key was released. Waits for a ``ask_begin`` still in flight."""
        begin, self._begin = self._begin, None
        if begin is None:
            return
        ask_id = await begin
        if ask_id is None:
            return
        try:
            await self._control.request({"cmd": "ask_end"})
        except control.ControlError as e:
            self._show(AskView(AskPhase.FAILED, str(e)))
            return
        except OSError:
            self._lost()
            return
        self._released = self._clock()
        if self._id == ask_id:
            self._show(AskView(AskPhase.WAITING))

    async def _ask_begin(self) -> str | None:
        try:
            try:
                reply = await self._control.request({"cmd": "ask_begin"})
            except control.ControlError as e:
                if "already open" not in str(e):
                    raise
                # Left open by an app that died holding the key: close it, then ask.
                await self._control.request({"cmd": "ask_end"})
                reply = await self._control.request({"cmd": "ask_begin"})
        except control.ControlError as e:
            message = str(e)
            # Covers both refusals: voice off, and no streaming session (local-only).
            if "voice" in message:
                self._show(AskView(AskPhase.VOICE_OFF))
            else:
                self._show(AskView(AskPhase.FAILED, message))
            return None
        except OSError:
            self._lost()
            return None
        self._id = str(reply["id"])
        return self._id

    # --- the daemon's events -----------------------------------------------------------------

    def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "voice_state":
            if not self.session:
                self._warm()  # a session started: load the voice before the first reply
            self.session = True
            self.voice = bool(event.get("available"))
        elif kind == "ask_reply":
            if event.get("id") != self._id or self.view.phase is not AskPhase.WAITING:
                return
            self._id = None
            text = str(event.get("text") or "")
            if (
                text.strip()
                and self._speaker is not None
                and event.get("outcome") in (SPOKEN_OUTCOMES)
            ):
                self._show(AskView(AskPhase.WAITING))
                self._delivery = asyncio.create_task(self._deliver(str(event["id"]), text))
            elif text.strip():
                self._show(AskView(AskPhase.REPLY, text))
            elif event.get("outcome") == "empty":
                self._show(AskView(AskPhase.NOTHING_HEARD))
            else:
                self._show(AskView(AskPhase.NO_ANSWER))

    def on_session_ended(self) -> None:
        """The daemon's subscription closed, or there is no daemon."""
        self.session = False
        self.voice = False
        if self._delivery is not None and not self._delivery.done():
            self._delivery.cancel()  # stops the speech; there is no daemon to report to
            self._lost()
        elif self.view.phase in (AskPhase.LISTENING, AskPhase.WAITING):
            self._lost()

    async def close(self) -> None:
        self._cancel_timer()
        await self._stop_delivery()
        for task in (self._begin, self._warming):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self._reports is not None:
            await asyncio.gather(self._reports, return_exceptions=True)

    # --- speaking the reply --------------------------------------------------------------------

    def _warm(self) -> None:
        if self._speaker is None or (self._warming is not None and not self._warming.done()):
            return
        self._warming = asyncio.create_task(self._warm_up(self._speaker))

    @staticmethod
    async def _warm_up(speaker: Speaker) -> None:
        try:
            await speaker.warm()
        except Exception as e:  # speaking will fail too, and say so then
            log.warning("the voice did not load: %s", e)

    async def _deliver(self, ask_id: str, text: str) -> None:
        assert self._speaker is not None
        route, sink = await self._read_route()
        if not route.closed or sink is None:
            self._not_spoken(ask_id, text, NotSpoken.ROUTE, "route")
            return
        if not await self._only_the_mic_is_recorded():
            self._not_spoken(ask_id, text, NotSpoken.TRANSCRIPT, "route")
            return
        self._show(AskView(AskPhase.SPEAKING, text))

        def first_audio() -> None:
            latency = int((self._clock() - self._released) * 1000)
            self._report(ask_id, "started", latency_ms=max(0, latency))

        self._speaking = ask_id
        try:
            played = await speak_guarded(
                self._speaker, text, sink, self._read_route, first_audio, poll=self._poll
            )
        finally:
            self._speaking = None
        if played is Played.DONE:
            self._report(ask_id, "done")
            self._show(AskView(AskPhase.REPLY, text))
        elif played is Played.INTERRUPTED:
            self._not_spoken(ask_id, text, NotSpoken.INTERRUPTED, "interrupted")
        else:
            self._not_spoken(ask_id, text, NotSpoken.SPEECH_FAILED, "tts_failed")

    async def _only_the_mic_is_recorded(self) -> bool:
        """Fails closed: a daemon that doesn't say counts as recording this box's output."""
        try:
            status = await self._control.request({"cmd": "status"})
        except (control.ControlError, OSError):
            return False
        return status.get("system") is False

    def _not_spoken(self, ask_id: str, text: str, why: NotSpoken, reason: str) -> None:
        self._report(ask_id, "skipped", reason=reason)
        self._show(AskView(AskPhase.REPLY, text, why))

    def _report(self, ask_id: str, state: str, **fields: Any) -> None:
        """Sends ``ask_played``, after any still in flight. Best effort: the daemon may be gone."""
        before = self._reports
        payload = {"cmd": "ask_played", "id": ask_id, "state": state, **fields}

        async def send() -> None:
            if before is not None:
                await asyncio.gather(before, return_exceptions=True)
            try:
                await self._control.request(payload)
            except (control.ControlError, OSError) as e:
                log.warning("ask_played not sent: %s", e)

        self._reports = asyncio.create_task(send())

    async def _stop_delivery(self) -> None:
        task, self._delivery = self._delivery, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # --- the pill ----------------------------------------------------------------------------

    def _lost(self) -> None:
        self._id = None
        self._show(AskView(AskPhase.NO_SESSION))

    def _show(self, view: AskView) -> None:
        self._cancel_timer()
        self.view = view
        self._on_change(view)
        if view.phase is AskPhase.WAITING:
            self._timer = asyncio.create_task(
                self._after(self._wait_timeout, AskView(AskPhase.NO_ANSWER))
            )
        elif (delay := self._dismiss(view)) is not None:
            self._timer = asyncio.create_task(self._after(delay, AskView(AskPhase.IDLE)))

    def _dismiss(self, view: AskView) -> float | None:
        default = view.dismiss_after
        if default is None:
            return None
        if view.phase is AskPhase.REPLY:
            return self._reply_s if self._reply_s is not None else default
        return self._message_s if self._message_s is not None else default

    async def _after(self, delay: float, view: AskView) -> None:
        await asyncio.sleep(delay)
        self._timer = None  # this task is finishing: _show must not cancel it
        if view.phase is AskPhase.NO_ANSWER:
            self._id = None
        self._show(view)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


async def watch(path: Path, ask: AskController, retry: float = 1.0) -> None:
    """Feeds ``ask`` the daemon's events for as long as it runs, and finds the next daemon."""
    while True:
        with contextlib.suppress(OSError, ValueError):
            async for event in control.subscribe(path):
                ask.on_event(event)
        ask.on_session_ended()
        await asyncio.sleep(retry)
