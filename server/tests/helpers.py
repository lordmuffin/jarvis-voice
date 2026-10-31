import asyncio
import json
from collections.abc import Callable
from typing import Any

import websockets

from jarvis_live.protocol import encode_frame
from jarvis_live.replay import ReplayProducer, split_frames


async def eventually(cond: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


class Viewer:
    """Connects as a viewer and records every JSON message until the socket closes."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.events: list[dict[str, Any]] = []
        self.connected = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Viewer":
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self.connected.wait(), 10)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._task is not None
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        async with websockets.connect(self.url) as ws:
            self.connected.set()
            try:
                async for raw in ws:
                    self.events.append(json.loads(raw))
            except websockets.ConnectionClosed:
                pass

    def of_type(self, type_: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type_]


async def open_viewer(rp: ReplayProducer, session_id: str) -> Viewer:
    ticket = await rp.ticket(session_id, "viewer")
    return Viewer(rp._ws_url(session_id, ticket))


class RawProducer:
    """Hand-driven producer connection for protocol-level tests."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws

    @classmethod
    async def connect(
        cls, rp: ReplayProducer, session_id: str, resume: dict[str, int | None] | None = None
    ) -> tuple["RawProducer", dict[str, Any]]:
        ticket = await rp.ticket(session_id)
        ws = await websockets.connect(rp._ws_url(session_id, ticket))
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "protocol": 1,
                    "device": "raw",
                    "codec": "pcm16le_16k",
                    "resume": resume or {"mic": None, "system": None},
                }
            )
        )
        return cls(ws), json.loads(await ws.recv())

    async def send_pcm(self, channel: int, pcm: bytes, seq0: int = 0, frame_ms: int = 100) -> int:
        """Send ``pcm`` as consecutive frames; returns the next seq."""
        frames = split_frames(pcm, frame_ms)
        for i, f in enumerate(frames):
            await self.ws.send(encode_frame(channel, seq0 + i, (seq0 + i) * frame_ms, f))
        return seq0 + len(frames)

    async def send_json(self, obj: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(obj))

    async def recv_until(self, type_: str, timeout: float = 10.0) -> dict[str, Any]:
        async def go() -> dict[str, Any]:
            while True:
                msg: dict[str, Any] = json.loads(await self.ws.recv())
                if msg["type"] == type_:
                    return msg

        return await asyncio.wait_for(go(), timeout)

    async def close(self) -> None:
        await self.ws.close()
