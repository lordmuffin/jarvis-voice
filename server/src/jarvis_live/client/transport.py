"""Resumable producer connection for one session (port of LiveConnection.swift).

Audio enters through the shared ``Outbox`` (followed by ``kick()``); the outbox is the only record
of what was captured, and only the server's cumulative ``ack`` frees data. On every (re)connect the
connection fetches a fresh ticket, sends ``hello`` with the outbox's acked seqs as ``resume``,
applies the server's ``hello_ack`` (authoritative), then replays everything still unacked before
streaming new frames.

This is the only module in ``jarvis_live.client`` allowed to touch the network.
"""

import asyncio
import contextlib
import random
from dataclasses import dataclass
from typing import Literal

import httpx
import websockets

from jarvis_live.client.outbox import CHANNELS, Outbox
from jarvis_live.protocol import (
    Ack,
    ClientMessage,
    Cursor,
    DraftSegment,
    End,
    Hello,
    HelloAck,
    Marker,
    ServerMessage,
    encode_frame,
    server_message_adapter,
)

State = Literal["idle", "connecting", "connected", "reconnecting", "ended", "failed", "stopped"]
CHANNEL_INDEX = {"mic": 0, "system": 1}
FATAL_HTTP = (401, 403, 404)
FATAL_WS_CLOSE = (4401, 4404)  # api/stream.py WS_UNAUTHORIZED, WS_NOT_FOUND
SEND_BATCH = 16


@dataclass(frozen=True)
class ReconnectPolicy:
    """Exponential backoff with additive jitter: 0.5 s, 1 s, 2 s ... capped at 30 s."""

    initial_delay: float = 0.5
    max_delay: float = 30.0
    jitter_fraction: float = 0.25

    def delay(self, attempt: int, random: float | None = None) -> float:
        r = _rand() if random is None else random
        base = min(self.max_delay, self.initial_delay * 2.0 ** min(max(attempt, 0), 30))
        return min(self.max_delay, base * (1 + self.jitter_fraction * r))


def _rand() -> float:
    return random.random()  # noqa: S311  (jitter, not security)


class _Fatal(Exception):
    pass


class ProducerConnection:
    def __init__(
        self,
        server: str,
        token: str,
        session_id: str,
        outbox: Outbox,
        *,
        device: str,
        max_unacked_ms: int = 5_000,
        reconnect: ReconnectPolicy = ReconnectPolicy(),  # noqa: B008
        handshake_timeout: float = 10.0,
        end_grace: float = 10.0,
    ) -> None:
        self.server = server.rstrip("/")
        self.session_id = session_id
        self.outbox = outbox
        self.device = device
        self.max_unacked_ms = max_unacked_ms
        self.reconnect = reconnect
        self.handshake_timeout = handshake_timeout
        self.end_grace = end_grace
        self._auth = {"Authorization": f"Bearer {token}"}

        self.state: State = "idle"
        self.failure: str | None = None
        self.failures = 0  # consecutive failed attempts; 0 while connected
        self.events: asyncio.Queue[ServerMessage] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._ws: websockets.ClientConnection | None = None
        self._wake = asyncio.Event()
        self._control: list[ClientMessage] = []
        self._end_requested = False
        self._end_sent = False
        self._done = asyncio.Event()

    # --- public API -----------------------------------------------------------------------

    def start(self) -> None:
        """Start connecting. Idempotent."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def kick(self) -> None:
        """Wake the send loop after frames were appended to the outbox."""
        self._wake.set()

    def send_marker(self, t_ms: int, label: str) -> None:
        self._control.append(Marker(t_ms=t_ms, label=label))
        self.kick()

    def send_draft(self, draft: DraftSegment) -> None:
        self._control.append(draft)
        self.kick()

    async def end(self, timeout: float = 30.0) -> bool:
        """Flush, send ``end``, wait for every ack (and briefly for ``final_note``/close).

        Returns True if the session ended cleanly. If the server cannot be reached or never acks
        within ``timeout`` the connection is dropped and this returns False; the outbox stays
        resumable, so nothing recorded is lost."""
        self._end_requested = True
        self.start()
        self.kick()
        try:
            await asyncio.wait_for(self._done.wait(), timeout)
        except TimeoutError:
            await self.stop()
            return False
        return self.state == "ended"

    async def wait_closed(self) -> None:
        """Returns when the connection ended, failed, or was stopped."""
        await self._done.wait()

    async def stop(self) -> None:
        """Drop the connection without ending the session; the outbox stays resumable."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self.state not in ("ended", "failed"):
            self._set_state("stopped")

    def abort_connection(self) -> None:
        """Kill the TCP connection without a close handshake (network drop); it then reconnects."""
        if self._ws is not None:
            self._ws.transport.abort()

    # --- connection loop ------------------------------------------------------------------

    def _set_state(self, new: State) -> None:
        self.state = new
        if new in ("ended", "failed", "stopped"):
            self._done.set()

    async def _run(self) -> None:
        while True:
            self._set_state("connecting" if self.failures == 0 else "reconnecting")
            try:
                await self._run_once()
                self._set_state("ended")
                return
            except asyncio.CancelledError:
                raise
            except _Fatal as e:
                self.failure = str(e)
                self._set_state("failed")
                return
            except Exception:  # noqa: BLE001  transient: back off and retry
                pass
            delay = self.reconnect.delay(self.failures)
            self.failures += 1
            self._set_state("reconnecting")
            await asyncio.sleep(delay)

    async def _ticket(self) -> str:
        async with httpx.AsyncClient(base_url=self.server, headers=self._auth, timeout=10) as c:
            r = await c.post(f"/v1/sessions/{self.session_id}/ticket", json=ticket_body())
        if r.status_code in FATAL_HTTP:
            raise _Fatal(f"HTTP {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return str(r.json()["ticket"])

    def _ws_url(self, ticket: str) -> str:
        base = self.server.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        return f"{base}/v1/sessions/{self.session_id}/stream?ticket={ticket}"

    async def _run_once(self) -> None:
        ticket = await self._ticket()
        self._end_sent = False  # re-sent on a new connection if it was not fully acked
        try:
            async with websockets.connect(self._ws_url(ticket), max_size=None) as ws:
                self._ws = ws
                await self._converse(ws)
        except websockets.ConnectionClosed as e:
            if e.rcvd is not None and e.rcvd.code in FATAL_WS_CLOSE:
                raise _Fatal(f"server closed the stream with {e.rcvd.code}") from e
            raise
        finally:
            self._ws = None

    async def _converse(self, ws: websockets.ClientConnection) -> None:
        resume = Cursor(**self.outbox.acked_seqs())
        await ws.send(
            Hello(
                protocol=1, device=self.device, codec="pcm16le_16k", resume=resume
            ).model_dump_json()
        )
        ack = await asyncio.wait_for(self._recv_hello_ack(ws), self.handshake_timeout)
        for channel in CHANNELS:
            seq = getattr(ack.acked, channel)
            if seq is not None:
                self.outbox.mark_acked(channel, seq)
        self.failures = 0
        self._set_state("connected")

        start = {c: _after(self.outbox.acked_seq(c)) for c in CHANNELS}
        receiver = asyncio.create_task(self._receive_loop(ws))
        sender = asyncio.create_task(self._send_and_finish(ws, start))
        try:
            done, _ = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()  # a failure means reconnect; a clean sender return means session over
        finally:
            for t in (receiver, sender):
                t.cancel()
            await asyncio.gather(receiver, sender, return_exceptions=True)

    async def _recv_hello_ack(self, ws: websockets.ClientConnection) -> HelloAck:
        async for raw in ws:
            msg = parse_server_message(raw)
            if isinstance(msg, HelloAck):
                return msg
            if msg is not None and msg.type == "error":
                raise ConnectionError(f"{msg.code}: {msg.message}")
        raise ConnectionError("closed before hello_ack")

    async def _receive_loop(self, ws: websockets.ClientConnection) -> None:
        async for raw in ws:
            msg = parse_server_message(raw)
            if msg is None:
                continue
            if isinstance(msg, Ack):
                self.outbox.mark_acked(msg.channel, msg.seq)
                self._wake.set()  # backpressure may have lifted
            elif not isinstance(msg, HelloAck):
                self.events.put_nowait(msg)
        # Server closed the socket. Normal after `end`; otherwise the caller reconnects.
        if not self._end_sent:
            raise ConnectionError("server closed the connection")

    async def _send_and_finish(
        self, ws: websockets.ClientConnection, start: dict[str, int]
    ) -> None:
        await self._send_loop(ws, start)
        # `end` is sent and fully acked; give the server a moment to emit final_note / close.
        await asyncio.sleep(self.end_grace)

    async def _send_loop(
        self, ws: websockets.ClientConnection, next_to_send: dict[str, int]
    ) -> None:
        while True:
            progressed = False
            while self._control:
                msg = self._control.pop(0)
                try:
                    await ws.send(msg.model_dump_json())
                except Exception:
                    self._control.insert(0, msg)
                    raise
                progressed = True

            for channel in CHANNELS:
                nxt = next_to_send[channel]
                in_flight = self.outbox.unacked_duration_ms(channel, through=nxt - 1) if nxt else 0
                if in_flight > self.max_unacked_ms:
                    continue  # backpressure
                for f in self.outbox.unacked(channel, start=nxt, limit=SEND_BATCH):
                    if in_flight > self.max_unacked_ms:
                        break
                    await ws.send(encode_frame(CHANNEL_INDEX[channel], f.seq, f.t_ms, f.pcm))
                    next_to_send[channel] = f.seq + 1
                    in_flight += f.duration_ms
                    progressed = True

            if self._end_requested and not progressed:
                all_sent = all(
                    not self.outbox.unacked(c, start=next_to_send[c], limit=1) for c in CHANNELS
                )
                all_acked = all(self.outbox.unacked_duration_ms(c) == 0 for c in CHANNELS)
                if all_sent and not self._control:
                    if not self._end_sent:
                        await ws.send(End().model_dump_json())
                        self._end_sent = True
                    if all_acked:
                        return

            if not progressed:
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    # Re-check periodically too, so a lost wake can never stall the stream.
                    await asyncio.wait_for(self._wake.wait(), 0.5)


def make_hello(device: str, resume: dict[str, int | None]) -> Hello:
    return Hello(
        protocol=1,
        device=device,
        codec="pcm16le_16k",
        resume=Cursor(mic=resume.get("mic"), system=resume.get("system")),
    )


def ticket_body(role: str = "producer") -> dict[str, str]:
    return {"role": role}


def session_create_body(title: str | None, *, meeting: bool, system: bool) -> dict[str, object]:
    """``POST /v1/sessions`` body. ``system`` adds the system channel; it never replaces mic."""
    body: dict[str, object] = {
        "mode": "meeting" if meeting else "solo",
        "channels": ["mic", "system"] if system else ["mic"],
    }
    if title is not None:  # the schema allows an absent title, not a null one
        body["title"] = title
    return body


async def create_session(server: str, token: str, body: dict[str, object]) -> str:
    """``POST /v1/sessions``; returns the new session id."""
    async with httpx.AsyncClient(
        base_url=server.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=15
    ) as c:
        r = await c.post("/v1/sessions", json=body)
    r.raise_for_status()
    return str(r.json()["id"])


def _after(acked: int | None) -> int:
    return 0 if acked is None else acked + 1


def parse_server_message(raw: str | bytes) -> ServerMessage | None:
    """A server message, or None if it is unknown or malformed (ignored, not fatal)."""
    try:
        return server_message_adapter.validate_json(raw)
    except ValueError:
        return None  # unknown or malformed server message: ignore rather than drop the stream


__all__ = [
    "DraftSegment",
    "End",
    "Marker",
    "ProducerConnection",
    "ReconnectPolicy",
    "State",
    "create_session",
    "make_hello",
    "parse_server_message",
    "session_create_body",
    "ticket_body",
]
