import asyncio
import contextlib
import uuid

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from jarvis_live.bus import Subscription
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.ingest.hub import IngestHub, SessionIngest
from jarvis_live.ingest.store import CHANNEL_NAMES, SeqGap, StoreClosed, save_draft, save_marker
from jarvis_live.logging_setup import SessionLogger, session_logger
from jarvis_live.protocol import (
    Ack,
    DraftSegment,
    End,
    Error,
    FrameError,
    Hello,
    HelloAck,
    Marker,
    client_message_adapter,
    decode_frame,
)

router = APIRouter()

WS_POLICY = 1008
WS_BAD_DATA = 1007
WS_UNAUTHORIZED = 4401
WS_NOT_FOUND = 4404


async def _recv(ws: WebSocket) -> str | bytes | None:
    """Next text/binary message, or None once the peer is gone."""
    try:
        msg = await ws.receive()
    except (WebSocketDisconnect, RuntimeError):
        return None
    if msg["type"] == "websocket.disconnect":
        return None
    data: str | bytes | None = msg.get("text")
    return data if data is not None else msg.get("bytes")


async def _sender(ws: WebSocket, sub: Subscription) -> None:
    """Single writer for the socket: everything (acks, bus events) flows through ``sub``."""
    while (event := await sub.get()) is not None:
        try:
            await ws.send_text(event.model_dump_json())
        except (WebSocketDisconnect, RuntimeError):
            return


def _error(code: str, message: str) -> Error:
    return Error(code=code, message=message)


@router.websocket("/v1/sessions/{session_id}/stream")
async def stream(ws: WebSocket, session_id: uuid.UUID, ticket: str = "") -> None:
    state = ws.app.state
    t = state.tickets.redeem(ticket, session_id)
    if t is None:
        await ws.close(code=WS_UNAUTHORIZED)
        return
    async with state.sessionmaker() as db:
        row = await db.get(SessionRow, session_id)
    if row is None:
        await ws.close(code=WS_NOT_FOUND)
        return
    log = session_logger(__name__, session_id)
    # Subscribe before accepting so a client that connects and immediately publishes cannot
    # miss events.
    sub = state.bus.subscribe(str(session_id))
    sender = asyncio.create_task(_sender(ws, sub))
    try:
        await ws.accept()
        if t.role == "viewer":
            await _viewer(ws, sub, log)
        else:
            await _producer(ws, sub, state.hub, row, log)
    finally:
        sub.close()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(sender, timeout=5)
        sender.cancel()
        if ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await ws.close()


async def _reject(
    sub: Subscription, ws: WebSocket, code: str, message: str, log: SessionLogger
) -> None:
    log.warning("closing connection: %s: %s", code, message)
    sub.put(_error(code, message))
    sub.close()  # sender flushes the error, then the caller closes the socket


async def _viewer(ws: WebSocket, sub: Subscription, log: SessionLogger) -> None:
    log.info("viewer connected")
    if await _recv(ws) is not None:
        await _reject(sub, ws, "read_only", "viewers cannot send messages", log)
    log.info("viewer disconnected")


async def _producer(
    ws: WebSocket, sub: Subscription, hub: IngestHub, row: SessionRow, log: SessionLogger
) -> None:
    settings = ws.app.state.settings
    first = await _recv(ws)
    if first is None:
        return
    try:
        hello = client_message_adapter.validate_json(first if isinstance(first, str) else b"")
    except ValidationError:
        hello = None
    if not isinstance(hello, Hello):
        await _reject(sub, ws, "hello_required", "first message must be a valid hello", log)
        return
    if row.status != "live":
        await _reject(sub, ws, "session_not_live", f"session is {row.status}", log)
        return

    ing = await hub.get(row.id, list(row.channels))
    if ing.producer is not None and ing.producer is not ws:
        log.info("superseding previous producer connection")
        with contextlib.suppress(RuntimeError):
            await ing.producer.close(code=4409)
    ing.producer = ws
    cursor = ing.store.cursor()
    sub.put(HelloAck(session=str(row.id), acked={"mic": cursor["mic"], "system": cursor["system"]}))  # type: ignore[arg-type]
    log.info("producer connected", extra={"cursor": cursor, "resume": hello.resume.model_dump()})

    dirty: set[str] = set()
    ack_task = asyncio.create_task(_ack_loop(sub, ing, dirty, settings.ack_interval_ms / 1000))
    try:
        ended = await _producer_loop(ws, sub, hub, ing, dirty, log)
    finally:
        ack_task.cancel()
        await asyncio.gather(ack_task, return_exceptions=True)
        if ing.producer is ws:
            ing.producer = None
        await ing.store.maybe_fsync(force=True)
    if ended:
        _flush_acks(sub, ing, dirty)
    log.info("producer disconnected", extra={"ended": ended})


def _flush_acks(sub: Subscription, ing: SessionIngest, dirty: set[str]) -> None:
    cursor = ing.store.cursor()
    for channel in sorted(dirty):
        seq = cursor[channel]
        if seq is not None:
            sub.put(Ack(channel=channel, seq=seq))  # type: ignore[arg-type]
    dirty.clear()


async def _ack_loop(
    sub: Subscription, ing: SessionIngest, dirty: set[str], interval_s: float
) -> None:
    while True:
        await asyncio.sleep(interval_s)
        _flush_acks(sub, ing, dirty)
        await ing.store.maybe_fsync()


async def _producer_loop(
    ws: WebSocket,
    sub: Subscription,
    hub: IngestHub,
    ing: SessionIngest,
    dirty: set[str],
    log: SessionLogger,
) -> bool:
    """Returns True if the producer ended the session."""
    sid = str(ing.session_id)
    bus = ws.app.state.bus
    sm = ws.app.state.sessionmaker
    while (msg := await _recv(ws)) is not None:
        if isinstance(msg, bytes):
            try:
                frame = decode_frame(msg)
            except FrameError as e:
                await _reject(sub, ws, "bad_frame", str(e), log)
                return False
            channel = CHANNEL_NAMES[frame.channel]
            if channel not in ing.channels:
                await _reject(sub, ws, "bad_channel", f"session has no {channel} channel", log)
                return False
            try:
                outcome = await ing.store.append(frame)
            except SeqGap as e:
                await _reject(sub, ws, "seq_gap", str(e), log)
                return False
            except StoreClosed:
                await _reject(sub, ws, "session_not_live", "session has ended", log)
                return False
            if outcome == "stored":
                ing.pipeline.feed(channel, frame.pcm)
            dirty.add(channel)
            continue

        try:
            parsed = client_message_adapter.validate_json(msg)
        except ValidationError as e:
            await _reject(sub, ws, "bad_message", e.errors()[0]["msg"], log)
            return False
        if isinstance(parsed, DraftSegment):
            if parsed.final:  # partial drafts are only relayed, not stored
                await save_draft(sm, ing.session_id, parsed)
            bus.publish(sid, parsed)
        elif isinstance(parsed, Marker):
            await save_marker(sm, ing.session_id, parsed)
            bus.publish(sid, parsed)
        elif isinstance(parsed, End):
            await hub.end(ing.session_id, caller=ws)
            return True
        else:
            await _reject(sub, ws, "unexpected_hello", "hello already received", log)
            return False
    return False
