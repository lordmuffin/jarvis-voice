"""Hold to ask through the daemon: control commands in, ``ask_*`` messages on the stream out.

The server is ``FakeStream``; the recorder is a script that emits one second of audio."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from jarvis_live.client import control
from jarvis_live.client.capture import FRAME_MS
from jarvis_live.client.config import ClientConfig, runtime_dir
from jarvis_live.client.daemon import Daemon, SessionOptions
from jarvis_live.client.render import TailRenderer
from jarvis_live.protocol import AskReply
from tests.audio import silence
from tests.client.fake_stream import FakeStream
from tests.client.test_daemon import _env, _recorder, _until, _until_up
from tests.helpers import eventually

AUDIO_MS = 1000


@asynccontextmanager
async def running(tmp_path: Path, fake: FakeStream, mode: str = "stream") -> AsyncIterator[Path]:
    """A daemon streaming one second of audio to ``fake``; yields its control socket."""
    env = _env(tmp_path)
    cfg = ClientConfig(server=fake.url, token="t", mode=mode, device="spark")  # noqa: S106
    d = Daemon(cfg, env, SessionOptions(), recorder=_recorder(tmp_path, silence(AUDIO_MS / 1000)))
    task = asyncio.create_task(d.run())
    try:
        await _until_up(env, task)
        if mode == "stream":
            await eventually(lambda: len(fake.frames) == AUDIO_MS // FRAME_MS)
        yield runtime_dir(env) / "ctl.sock"
    finally:
        d.request_stop()
        await asyncio.wait_for(task, 30)


async def ask(sock: Path, cmd: str, **kw: Any) -> dict[str, Any]:
    return await control.request(sock, {"cmd": cmd, **kw})


async def voice_up(sock: Path) -> None:
    async def up() -> bool:
        return bool((await ask(sock, "status")).get("voice"))

    await _until(up)


async def test_the_stream_asks_for_the_voice_feature(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake):
        assert fake.features == ["voice_ask"]


async def test_begin_and_end_mark_the_ask_on_the_audio_clock(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake) as sock:
        await voice_up(sock)
        begun = await ask(sock, "ask_begin")
        ended = await ask(sock, "ask_end")
        await eventually(lambda: bool(fake.of("ask_end")))
    # One second of audio has been captured: the ask lands at its end, not on the wall clock.
    assert AUDIO_MS <= begun["t_ms"] <= ended["t_ms"] <= AUDIO_MS + FRAME_MS
    assert ended["id"] == begun["id"]
    assert fake.of("ask_begin") == [{"type": "ask_begin", "id": begun["id"], "t_ms": begun["t_ms"]}]
    assert fake.of("ask_end") == [{"type": "ask_end", "id": ended["id"], "t_ms": ended["t_ms"]}]


async def test_each_ask_gets_a_fresh_id(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake) as sock:
        await voice_up(sock)
        first = (await ask(sock, "ask_begin"))["id"]
        await ask(sock, "ask_end")
        second = (await ask(sock, "ask_begin"))["id"]
        await ask(sock, "ask_end")
    assert first != second


async def test_no_ask_while_voice_is_unavailable(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream(voice=False)
    async with running(tmp_path, fake) as sock:
        await eventually(lambda: fake.connected)
        with pytest.raises(control.ControlError, match="voice"):
            await ask(sock, "ask_begin")
        assert (await ask(sock, "status"))["voice"] is False
    assert fake.of("ask_begin") == []


async def test_no_ask_in_a_local_only_session(tmp_path: Path, no_network: list[str]) -> None:
    fake = FakeStream()
    async with running(tmp_path, fake, mode="local") as sock:
        with pytest.raises(control.ControlError, match="voice"):
            await ask(sock, "ask_begin")
        with pytest.raises(control.ControlError):
            await ask(sock, "ask_played", id="a1", state="skipped", reason="route")
    assert no_network == []


async def test_no_second_ask_while_one_is_open(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake) as sock:
        await voice_up(sock)
        await ask(sock, "ask_begin")
        with pytest.raises(control.ControlError, match="open"):
            await ask(sock, "ask_begin")
        await ask(sock, "ask_end")
        await eventually(lambda: bool(fake.of("ask_end")))
    assert len(fake.of("ask_begin")) == 1


async def test_ask_end_without_an_open_ask_is_refused(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake) as sock:
        await voice_up(sock)
        with pytest.raises(control.ControlError, match="no ask"):
            await ask(sock, "ask_end")
    assert fake.of("ask_end") == []


async def test_a_subscriber_gets_the_voice_state_and_the_reply(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake) as sock:
        await voice_up(sock)
        seen: list[dict[str, Any]] = []

        async def listen() -> None:
            async for ev in control.subscribe(sock):
                seen.append(ev)

        listener = asyncio.create_task(listen())
        try:
            await eventually(lambda: {"type": "voice_state", "available": True} in seen)
            begun = await ask(sock, "ask_begin")
            await ask(sock, "ask_end")
            fake.push(AskReply(id=begun["id"], outcome="answer", text="Priya runs it."))
            reply = {"type": "ask_reply", "id": begun["id"], "outcome": "answer"}
            await eventually(lambda: any(e.items() >= reply.items() for e in seen))
            fake.push_voice(False)
            await eventually(lambda: {"type": "voice_state", "available": False} in seen)
        finally:
            listener.cancel()


async def test_a_reply_is_not_replayed_to_a_later_subscriber(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake) as sock:
        await voice_up(sock)
        fake.push(AskReply(id="a1", outcome="answer", text="Old news."))
        await asyncio.sleep(0.3)
        first: dict[str, Any] = {}
        async for ev in control.subscribe(sock):
            first = ev
            break
    assert first == {"type": "voice_state", "available": True}


async def test_ask_played_is_sent_as_given(
    fake_stream: Callable[..., FakeStream], tmp_path: Path
) -> None:
    fake = fake_stream()
    async with running(tmp_path, fake) as sock:
        await voice_up(sock)
        await ask(sock, "ask_played", id="a1", state="started", latency_ms=2900)
        await ask(sock, "ask_played", id="a1", state="done")
        await ask(sock, "ask_played", id="a2", state="skipped", reason="route")
        with pytest.raises(control.ControlError, match="ask_played"):
            await ask(sock, "ask_played", id="a3", state="paused")
        await eventually(lambda: len(fake.of("ask_played")) == 3)
    played = fake.of("ask_played")
    assert [(p["id"], p["state"], p.get("latency_ms"), p.get("reason")) for p in played] == [
        ("a1", "started", 2900, None),
        ("a1", "done", None, None),
        ("a2", "skipped", None, "route"),
    ]
    assert all(AUDIO_MS <= p["t_ms"] <= AUDIO_MS + FRAME_MS for p in played)


def test_tail_prints_a_reply_on_its_own_line_marked_as_an_ask() -> None:
    r = TailRenderer()
    assert r.render(AskReply(id="a1", outcome="answer", text="Priya runs it.")) == [
        "  [ask] Priya runs it."
    ]
    assert r.render(AskReply(id="a2", outcome="empty", text="")) == ["  [ask] (empty)"]
