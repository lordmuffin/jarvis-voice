"""A fake Jarvis Live server for the headless client: create a session, mint a ticket, take one
producer stream. It acks every frame, records what the client sent, and lets a test push server
messages (``voice_state``, ``ask_reply``) down the open stream. Test double only.

It runs under uvicorn in its own thread and loop, so ``push`` is thread-safe."""

import asyncio
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from jarvis_live.protocol import Ack, Cursor, HelloAck, ServerMessage, VoiceState, decode_frame
from tests.conftest import _serve, _stop


class FakeStream:
    def __init__(self, voice: bool | None = True) -> None:
        self.url = ""
        self.voice = voice  # the voice_state sent after hello_ack; None sends none
        self.features: list[str] = []  # each stream's ``features`` query parameter
        self.sent: list[dict[str, Any]] = []  # JSON messages from the client, in order
        self.frames: list[tuple[int, int, int]] = []  # (channel, seq, t_ms)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._out: asyncio.Queue[str] | None = None

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m.get("type") == type_]

    @property
    def connected(self) -> bool:
        return self._out is not None

    def push(self, msg: ServerMessage) -> None:
        assert self._loop is not None and self._out is not None, "no stream is open"
        self._loop.call_soon_threadsafe(self._out.put_nowait, msg.model_dump_json())

    def push_voice(self, available: bool) -> None:
        self.push(VoiceState(available=available))

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/sessions")
        async def create() -> dict[str, str]:
            return {"id": str(uuid.uuid4())}

        @app.post("/v1/sessions/{sid}/ticket")
        async def ticket(sid: str) -> dict[str, str]:
            return {"ticket": "fake-ticket"}

        @app.websocket("/v1/sessions/{sid}/stream")
        async def stream(ws: WebSocket, sid: str, ticket: str = "", features: str = "") -> None:
            await ws.accept()
            self.features.append(features)
            await ws.receive_text()  # hello
            await ws.send_text(
                HelloAck(session=sid, acked=Cursor(mic=None, system=None)).model_dump_json()
            )
            self._loop, out = asyncio.get_running_loop(), asyncio.Queue[str]()
            self._out = out
            if self.voice is not None:
                await ws.send_text(json.dumps({"type": "voice_state", "available": self.voice}))
            writer = asyncio.create_task(self._write(ws, out))
            try:
                while True:
                    m = await ws.receive()
                    if m["type"] == "websocket.disconnect":
                        return
                    if m.get("bytes") is not None:
                        f = decode_frame(m["bytes"])
                        self.frames.append((f.channel, f.seq, f.t_ms))
                        channel = "mic" if f.channel == 0 else "system"
                        await out.put(Ack(channel=channel, seq=f.seq).model_dump_json())  # type: ignore[arg-type]
                    elif m.get("text") is not None:
                        msg = json.loads(m["text"])
                        self.sent.append(msg)
                        if msg.get("type") == "end":
                            await asyncio.sleep(0.1)  # let the last acks out
                            await ws.close()
                            return
            except WebSocketDisconnect:
                return
            finally:
                self._out = None
                writer.cancel()

        return app

    async def _write(self, ws: WebSocket, out: asyncio.Queue[str]) -> None:
        while True:
            await ws.send_text(await out.get())


@pytest.fixture
def fake_stream() -> Iterator[Callable[..., FakeStream]]:
    running: list[Any] = []

    def start(**kw: Any) -> FakeStream:
        fake = FakeStream(**kw)
        fake.url = f"http://127.0.0.1:{_serve(fake.app(), running)}"
        return fake

    yield start
    _stop(running)
