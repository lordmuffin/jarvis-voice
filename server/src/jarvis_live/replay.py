"""Producer client that streams a WAV file to a Jarvis Live server (also used by tests)."""

import asyncio
import json
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import websockets

from jarvis_live.protocol import BYTES_PER_MS, MIN_PAYLOAD, encode_frame

FRAME_MS = 100
CHANNEL_INDEX = {"mic": 0, "system": 1}


def read_wav_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 16_000):
            raise ValueError("WAV must be 16 kHz mono 16-bit PCM")
        return w.readframes(w.getnframes())


def split_frames(pcm: bytes, frame_ms: int = FRAME_MS) -> list[bytes]:
    """Split into frame_ms chunks; a short tail is zero-padded up to the 20 ms protocol minimum."""
    size = frame_ms * BYTES_PER_MS
    frames = [pcm[i : i + size] for i in range(0, len(pcm), size)]
    if frames and len(frames[-1]) < MIN_PAYLOAD:
        frames[-1] = frames[-1].ljust(MIN_PAYLOAD, b"\x00")
    return frames


@dataclass
class ReplayResult:
    session_id: str
    sent_frames: int = 0
    acked: dict[str, int | None] = field(default_factory=lambda: {"mic": None, "system": None})
    events: list[dict[str, Any]] = field(default_factory=list)  # non-ack server messages
    closed_by_server: bool = False


class ReplayProducer:
    def __init__(
        self,
        server: str,
        token: str,
        *,
        channel: str = "mic",
        speed: float = 4.0,
        frame_ms: int = FRAME_MS,
    ) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.channel = channel
        self.speed = speed
        self.frame_ms = frame_ms
        self._auth = {"Authorization": f"Bearer {token}"}

    async def create_session(self, title: str | None = None, mode: str = "solo") -> str:
        channels = ["mic"] if self.channel == "mic" else ["mic", "system"]
        async with httpx.AsyncClient(base_url=self.server, headers=self._auth) as c:
            r = await c.post(
                "/v1/sessions", json={"title": title, "mode": mode, "channels": channels}
            )
            r.raise_for_status()
            return str(r.json()["id"])

    async def ticket(self, session_id: str, role: str = "producer") -> str:
        async with httpx.AsyncClient(base_url=self.server, headers=self._auth) as c:
            r = await c.post(f"/v1/sessions/{session_id}/ticket", json={"role": role})
            r.raise_for_status()
            return str(r.json()["ticket"])

    def _ws_url(self, session_id: str, ticket: str) -> str:
        base = self.server.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        return f"{base}/v1/sessions/{session_id}/stream?ticket={ticket}"

    async def run(
        self,
        pcm: bytes,
        *,
        session_id: str | None = None,
        resume_from: int | None = None,
        abort_after_frames: int | None = None,
        end: bool = True,
        result: ReplayResult | None = None,
    ) -> ReplayResult:
        """Stream ``pcm`` as a producer.

        ``resume_from`` is the client's belief of the last acked seq (sent in ``hello.resume``);
        sending restarts at ``min(resume_from, server_acked) + 1`` so frames the server already
        stored are re-sent and must be ignored by it. ``abort_after_frames`` kills the TCP
        connection after that many frames have been sent (simulating a network drop)."""
        sid = session_id or await self.create_session()
        res = result or ReplayResult(session_id=sid)
        frames = split_frames(pcm, self.frame_ms)
        ticket = await self.ticket(sid)
        async with websockets.connect(self._ws_url(sid, ticket), max_size=None) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol": 1,
                        "device": "replay",
                        "codec": "pcm16le_16k",
                        "resume": {
                            "mic": resume_from if self.channel == "mic" else None,
                            "system": resume_from if self.channel == "system" else None,
                        },
                    }
                )
            )
            hello_ack = json.loads(await ws.recv())
            if hello_ack.get("type") != "hello_ack":
                res.events.append(hello_ack)
                return res
            server_acked = hello_ack["acked"][self.channel]
            start = _first_seq(resume_from, server_acked)

            reader = asyncio.create_task(self._read(ws, res))
            sent = 0
            for seq in range(start, len(frames)):
                if abort_after_frames is not None and sent >= abort_after_frames:
                    ws.transport.abort()
                    await asyncio.gather(reader, return_exceptions=True)
                    return res
                await ws.send(
                    encode_frame(CHANNEL_INDEX[self.channel], seq, seq * self.frame_ms, frames[seq])
                )
                sent += 1
                res.sent_frames += 1
                await asyncio.sleep(self.frame_ms / 1000 / self.speed)
            if end:
                await ws.send(json.dumps({"type": "end"}))
                await asyncio.wait_for(reader, timeout=60)  # server closes after draining
            else:
                await ws.close()
                await asyncio.gather(reader, return_exceptions=True)
        return res

    async def _read(self, ws: Any, res: ReplayResult) -> None:
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "ack":
                    res.acked[msg["channel"]] = msg["seq"]
                else:
                    res.events.append(msg)
        except websockets.ConnectionClosed:
            pass
        res.closed_by_server = True


def _first_seq(resume_from: int | None, server_acked: int | None) -> int:
    known = [x for x in (resume_from, server_acked) if x is not None]
    if len(known) < 2:  # one side has nothing: start from scratch
        return 0
    return min(known) + 1


async def replay(
    server: str, token: str, wav: Path, channel: str = "mic", speed: float = 4.0
) -> ReplayResult:
    producer = ReplayProducer(server, token, channel=channel, speed=speed)
    return await producer.run(read_wav_pcm(wav))
