"""Hold to ask from the desktop app (docs/plans/voice-push-to-talk.md). No Qt, no evdev.

The app does not talk to the server: the capture daemon does. ``AskController`` turns the ask
key's ``ASK_BEGIN`` and ``ASK_END`` (trigger.py) into the daemon's ``ask_begin`` and ``ask_end``
control commands, and ``watch`` feeds it the daemon's ``subscribe`` stream (``voice_state`` and
``ask_reply``). It asks only while a capture session is running with voice available, and
otherwise shows why and sends nothing. What it shows is an ``AskView`` for the pill."""

import asyncio
import contextlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from jarvis_live.client import control
from jarvis_live.client.config import runtime_dir

from jarvis_desktop.presentation import AskPhase, AskView

#: After release, how long to wait for the reply before saying there was none.
WAIT_TIMEOUT = 30.0


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

    # --- the key -----------------------------------------------------------------------------

    async def down(self) -> None:
        """``ASK_BEGIN``: the key has been held past the threshold."""
        self._id = None
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
            self.session = True
            self.voice = bool(event.get("available"))
        elif kind == "ask_reply":
            if event.get("id") != self._id or self.view.phase is not AskPhase.WAITING:
                return
            self._id = None
            text = str(event.get("text") or "")
            if text.strip():
                self._show(AskView(AskPhase.REPLY, text))
            elif event.get("outcome") == "empty":
                self._show(AskView(AskPhase.NOTHING_HEARD))
            else:
                self._show(AskView(AskPhase.NO_ANSWER))

    def on_session_ended(self) -> None:
        """The daemon's subscription closed, or there is no daemon."""
        self.session = False
        self.voice = False
        if self.view.phase in (AskPhase.LISTENING, AskPhase.WAITING):
            self._lost()

    async def close(self) -> None:
        self._cancel_timer()
        if self._begin is not None:
            self._begin.cancel()
            await asyncio.gather(self._begin, return_exceptions=True)

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
