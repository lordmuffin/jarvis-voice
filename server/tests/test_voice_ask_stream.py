"""Hold to ask over the real socket: markers on the audio in, ``ask_reply`` out.

The real app under uvicorn and a real Postgres; Whisper and the LLM are fakes."""

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import websockets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.db.models import CopilotItem, SegmentRow
from jarvis_live.protocol import encode_frame
from jarvis_live.replay import ReplayProducer
from jarvis_live.voice.ask_pass import ASK_KIND, AskAnswer
from jarvis_live.voice.schema import VoiceRequest
from tests.audio import silence, tone
from tests.conftest import ServerHandle
from tests.fakes import FakeLLM
from tests.helpers import eventually
from tests.test_voice_ask_pass import ScriptedSTT

SM = async_sessionmaker  # type: ignore[type-arg]
FRAME = 100 * 32
VOICE: dict[str, Any] = {"voice_enabled": True, "copilot_poll_s": 0.2}


class Producer:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.seq = 0
        self.t_ms = 0
        self.got: list[dict[str, Any]] = []
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(cls, server: ServerHandle, token: str, sid: str, features: str) -> "Producer":
        rp = ReplayProducer(server.url, token)
        ticket = await rp.ticket(sid)
        url = rp._ws_url(sid, ticket) + (f"&features={features}" if features else "")
        ws = await websockets.connect(url)
        hello = {
            "type": "hello",
            "protocol": 1,
            "device": "test",
            "codec": "pcm16le_16k",
            "resume": {"mic": None, "system": None},
        }
        await ws.send(json.dumps(hello))
        return cls(ws)

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                self.got.append(json.loads(raw))
        except websockets.ConnectionClosed:
            pass

    async def audio(self, pcm: bytes) -> None:
        for i in range(0, len(pcm), FRAME):
            await self.ws.send(encode_frame(0, self.seq, self.t_ms, pcm[i : i + FRAME]))
            self.seq += 1
            self.t_ms += 100

    async def send(self, **msg: Any) -> None:
        await self.ws.send(json.dumps(msg))

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [m for m in self.got if m["type"] == type_]

    async def close(self) -> None:
        await self.ws.close()
        self._reader.cancel()


async def new_session(server: ServerHandle, token: str, **body: Any) -> str:
    async with httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {token}"}
    ) as c:
        r = await c.post("/v1/sessions", json={"mode": "solo", "channels": ["mic"], **body})
        r.raise_for_status()
        return str(r.json()["id"])


async def ask(p: Producer, said_audio: bytes) -> None:
    await p.send(type="ask_begin", id="a1", t_ms=p.t_ms)
    await p.audio(said_audio)
    await p.send(type="ask_end", id="a1", t_ms=p.t_ms)
    await p.audio(silence(1.0))  # long enough for the segmenter to close the question


@pytest.fixture
def asking(make_server: Callable[..., ServerHandle]) -> Callable[..., ServerHandle]:
    def start(llm: FakeLLM, text: str = "Who is running the survey?", **cfg: Any) -> ServerHandle:
        return make_server(stt=ScriptedSTT(text), llm=llm, **{**VOICE, **cfg})

    return start


async def test_a_held_question_is_answered_and_its_playback_recorded(
    asking: Callable[..., ServerHandle], token: str, sm: SM
) -> None:
    server = asking(FakeLLM().queue("AskAnswer", AskAnswer(reply="Priya runs it.")))
    sid = await new_session(server, token)
    p = await Producer.open(server, token, sid, "voice_ask")
    await eventually(lambda: p.of("voice_state") == [{"type": "voice_state", "available": True}])

    await p.audio(silence(1.0))
    await ask(p, tone(2.0))
    await eventually(lambda: bool(p.of("ask_reply")), timeout=15)
    assert p.of("ask_reply") == [
        {"type": "ask_reply", "id": "a1", "outcome": "answer", "text": "Priya runs it."}
    ]

    await p.send(type="ask_played", id="a1", state="started", t_ms=p.t_ms, latency_ms=2900)
    await p.send(type="ask_played", id="a1", state="done", t_ms=p.t_ms + 1500)

    async def stored() -> dict[str, Any]:
        async with sm() as db:
            rows = (
                await db.execute(
                    select(CopilotItem.payload).where(
                        CopilotItem.session_id == uuid.UUID(sid), CopilotItem.kind == ASK_KIND
                    )
                )
            ).scalars()
            return next(iter(rows), {})

    for _ in range(50):
        if (await stored()).get("played") == "done":
            break
        await asyncio.sleep(0.1)
    row = await stored()
    assert (row["outcome"], row["latency_ms"], row["played"]) == ("answer", 2900, "done")
    await p.close()


async def test_a_client_that_did_not_ask_for_the_feature_hears_nothing_of_it(
    asking: Callable[..., ServerHandle], token: str
) -> None:
    server = asking(FakeLLM().queue("AskAnswer", AskAnswer(reply="Priya runs it.")))
    sid = await new_session(server, token)
    p = await Producer.open(server, token, sid, "")
    await p.audio(silence(1.0))
    await asyncio.sleep(0.5)
    assert p.of("voice_state") == []
    await p.close()


@pytest.mark.parametrize(
    ("cfg", "body"),
    [({"voice_enabled": False}, {}), ({}, {"restricted": True})],
    ids=["voice off", "restricted"],
)
async def test_asks_are_unavailable_where_voice_is_not(
    asking: Callable[..., ServerHandle], token: str, cfg: dict[str, Any], body: dict[str, Any]
) -> None:
    llm = FakeLLM().queue("AskAnswer", AskAnswer(reply="Priya runs it."))
    server = asking(llm, **cfg)
    sid = await new_session(server, token, **body)
    p = await Producer.open(server, token, sid, "voice_ask")
    await p.audio(silence(1.0))
    await asyncio.sleep(0.5)
    # A client sending one anyway is ignored, not cut off: the stream carries on.
    await ask(p, tone(2.0))
    await asyncio.sleep(1.0)
    assert p.of("voice_state") == []
    assert p.of("ask_reply") == []
    assert p.of("error") == []
    assert [c.schema for c in llm.calls if c.schema == "AskAnswer"] == []
    await p.close()


async def test_jarvis_heard_back_by_the_mic_stays_out_of_the_transcript(
    asking: Callable[..., ServerHandle], token: str, sm: SM
) -> None:
    """The fake Whisper hears the reply's own words in what the mic picks up while it plays."""
    reply = "Priya runs the survey."
    server = asking(FakeLLM().queue("AskAnswer", AskAnswer(reply=reply)), text=reply)
    sid = await new_session(server, token)
    p = await Producer.open(server, token, sid, "voice_ask")
    await eventually(lambda: bool(p.of("voice_state")))
    await p.audio(silence(1.0))
    await ask(p, tone(1.0))
    await eventually(lambda: bool(p.of("ask_reply")), timeout=15)

    async def segments() -> list[str]:
        async with sm() as db:
            return list(
                (
                    await db.execute(
                        select(SegmentRow.text).where(SegmentRow.session_id == uuid.UUID(sid))
                    )
                ).scalars()
            )

    for _ in range(100):  # the question itself stays, as ordinary speech
        if await segments():
            break
        await asyncio.sleep(0.1)
    asked = len(await segments())
    assert asked == 1
    await p.send(type="ask_played", id="a1", state="started", t_ms=p.t_ms, latency_ms=2000)
    await asyncio.sleep(0.2)
    await p.audio(tone(1.5) + silence(1.5))  # the reply, bleeding into the mic
    await p.send(type="ask_played", id="a1", state="done", t_ms=p.t_ms)
    await asyncio.sleep(1.5)
    assert len(await segments()) == asked
    await p.close()


async def test_a_meeting_speaks_only_when_asked(
    asking: Callable[..., ServerHandle], token: str
) -> None:
    """The wake phrase without the key files and replies on the copilot channel, never as speech."""
    llm = FakeLLM().queue(
        "VoiceRequest", VoiceRequest(target="pi", title="Check Flux", task="Why is Flux failing?")
    )
    server = asking(
        llm,
        text="Jarvis, start a Pi session to find out why Flux is failing.",
        voice_pi_enabled=True,
        actions_enabled=True,
        voice_settle_s=0.5,
    )
    sid = await new_session(server, token, mode="meeting")
    p = await Producer.open(server, token, sid, "voice_ask")
    await eventually(lambda: bool(p.of("voice_state")))
    await p.audio(silence(0.5) + tone(2.0) + silence(3.0))
    await eventually(lambda: any(c.schema == "VoiceRequest" for c in llm.calls), timeout=15)
    await asyncio.sleep(1.0)
    assert p.of("ask_reply") == []
    await p.close()
