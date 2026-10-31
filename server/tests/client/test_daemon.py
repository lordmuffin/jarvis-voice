import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.client import control
from jarvis_live.client.config import ClientConfig, runtime_dir, state_dir
from jarvis_live.client.daemon import Daemon, SessionOptions, find_resumable
from jarvis_live.client.outbox import Outbox
from jarvis_live.db.models import AudioChunk
from jarvis_live.protocol import BYTES_PER_MS
from tests.audio import TWO_UTTERANCES
from tests.client.conftest import script
from tests.conftest import ServerHandle

SM = async_sessionmaker  # type: ignore[type-arg]
FRAME = 100 * BYTES_PER_MS


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        "HOME": str(tmp_path),
    }


def _recorder(tmp_path: Path, pcm: bytes, name: str = "speech.pcm"):  # type: ignore[no-untyped-def]
    (tmp_path / name).write_bytes(pcm)
    fake = script(tmp_path / f"fake-{name}", f"cat {tmp_path / name}\nexec sleep 60\n")

    async def recorder(channel: str) -> list[str]:
        return [str(fake)]

    return recorder


def _daemon(tmp_path: Path, cfg: ClientConfig, pcm: bytes = TWO_UTTERANCES, **kw):  # type: ignore[no-untyped-def]
    env = _env(tmp_path)
    opts = kw.pop("opts", SessionOptions())
    return Daemon(cfg, env, opts, recorder=_recorder(tmp_path, pcm), **kw), env


async def _status(env: dict[str, str]) -> dict:  # type: ignore[type-arg]
    return await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "status"})


async def _until_up(env: dict[str, str], task: asyncio.Task[int]) -> dict:  # type: ignore[type-arg]
    async with asyncio.timeout(15):
        while True:
            if task.done():
                task.result()
                raise AssertionError("daemon exited early")
            try:
                st = await _status(env)
                if st["state"] != "starting":
                    return st
            except (OSError, control.ControlError):
                pass
            await asyncio.sleep(0.05)


async def _until(cond, timeout: float = 15.0) -> None:  # type: ignore[no-untyped-def]
    async with asyncio.timeout(timeout):
        while not await cond():
            await asyncio.sleep(0.05)


async def test_stream_session_reaches_the_server_and_stops_cleanly(
    server: ServerHandle, token: str, sm: SM, tmp_path: Path
) -> None:
    cfg = ClientConfig(server=server.url, token=token, mode="stream", device="spark")
    d, env = _daemon(tmp_path, cfg, opts=SessionOptions(title="From the Spark"))
    task = asyncio.create_task(d.run())
    st = await _until_up(env, task)
    assert st["mode"] == "STREAMING"
    sid = st["session"]
    assert st["dashboard_url"] == f"{server.url}/sessions/{sid}"
    assert st["title"] == "From the Spark"

    async def all_stored() -> bool:
        async with sm() as db:
            n = len(
                (
                    await db.execute(
                        select(AudioChunk.seq).where(AudioChunk.session_id == uuid.UUID(sid))
                    )
                ).all()
            )
        return n >= len(TWO_UTTERANCES) // FRAME

    await _until(all_stored)
    st = await _status(env)
    assert st["connection"] == "connected" and st["device"] == "spark"

    reply = await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "stop"}, timeout=60)
    assert reply["ok"] is True and reply["ended"] is True
    assert await asyncio.wait_for(task, 10) == 0
    assert not (runtime_dir(env) / "ctl.sock").exists()
    meta = json.loads((state_dir(env) / "sessions" / sid / "session.json").read_text())
    assert meta["ended"] is True


async def test_subscribers_receive_segments_until_the_daemon_stops(
    server: ServerHandle, token: str, tmp_path: Path
) -> None:
    cfg = ClientConfig(server=server.url, token=token, mode="stream")
    d, env = _daemon(tmp_path, cfg)
    task = asyncio.create_task(d.run())
    await _until_up(env, task)
    sock = runtime_dir(env) / "ctl.sock"
    seen: list[dict] = []  # type: ignore[type-arg]

    async def listen() -> None:
        async for ev in control.subscribe(sock):
            seen.append(ev)

    listener = asyncio.create_task(listen())
    try:
        await asyncio.sleep(1.0)  # let the stream get going
        await control.request(sock, {"cmd": "stop"}, timeout=60)
        await asyncio.wait_for(listener, 10)  # the daemon closes subscribers when it stops
        await asyncio.wait_for(task, 10)
    finally:
        listener.cancel()
        task.cancel()
    assert any(e["type"] == "segment" and e["text"] for e in seen), seen


async def test_local_only_session_never_touches_the_network(
    no_network: list[str], tmp_path: Path
) -> None:
    cfg = ClientConfig(mode="local", server="https://should-never-be-used.example", token="x")
    d, env = _daemon(tmp_path, cfg)
    task = asyncio.create_task(d.run())
    st = await _until_up(env, task)
    assert st["mode"] == "LOCAL ONLY" and "nothing leaves this machine" in st["mode_note"]
    assert st["dashboard_url"] is None and st["connection"] == "local"

    async def captured() -> bool:
        return (await _status(env))["unacked_ms"] >= 7_000

    await _until(captured)
    reply = await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "stop"}, timeout=30)
    assert reply["ok"] is True
    await asyncio.wait_for(task, 10)
    assert no_network == []
    ob = Outbox.reopen(st["session"], state_dir(env) / "sessions")
    assert ob.unacked_duration_ms("mic") >= 7_000  # kept on disk, never acked, never sent


async def test_the_guard_bites_when_a_stream_session_tries_the_network(
    no_network: list[str], tmp_path: Path
) -> None:
    cfg = ClientConfig(mode="stream", server="http://127.0.0.1:9", token="x")
    d, env = _daemon(tmp_path, cfg)
    with pytest.raises(Exception):  # noqa: B017  session creation fails under the guard
        await asyncio.wait_for(d.run(), 10)
    assert no_network, "the egress guard did not detect the connection attempt"


async def test_a_second_daemon_is_refused(tmp_path: Path, no_network: list[str]) -> None:
    cfg = ClientConfig(mode="local")
    d, env = _daemon(tmp_path, cfg)
    task = asyncio.create_task(d.run())
    await _until_up(env, task)
    second, _ = _daemon(tmp_path, cfg)
    with pytest.raises(RuntimeError, match="already running"):
        await second.run()
    await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "stop"}, timeout=30)
    await asyncio.wait_for(task, 10)


async def test_a_stale_socket_from_a_crash_does_not_block_a_new_daemon(
    tmp_path: Path, no_network: list[str]
) -> None:
    cfg = ClientConfig(mode="local")
    env = _env(tmp_path)
    run = runtime_dir(env)
    run.mkdir(parents=True)
    (run / "ctl.sock").write_text("")  # leftover, nobody listening
    (run / "daemon.pid").write_text("999999")
    d, _ = _daemon(tmp_path, cfg)
    task = asyncio.create_task(d.run())
    await _until_up(env, task)
    await control.request(run / "ctl.sock", {"cmd": "stop"}, timeout=30)
    await asyncio.wait_for(task, 10)


async def test_outbox_cap_fails_visibly_instead_of_filling_the_disk(
    tmp_path: Path, no_network: list[str]
) -> None:
    cfg = ClientConfig(mode="local")
    d, env = _daemon(tmp_path, cfg, max_outbox_bytes=5 * FRAME)
    task = asyncio.create_task(d.run())
    await _until_up(env, task)

    async def failed() -> bool:
        return (await _status(env))["state"] == "failed"

    await _until(failed)
    st = await _status(env)
    assert "outbox" in st["problem"]
    assert st["unacked_ms"] <= 10 * 100
    await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "stop"}, timeout=30)
    await asyncio.wait_for(task, 10)


async def test_resume_continues_the_unfinished_session_with_contiguous_seq_and_later_t_ms(
    tmp_path: Path, no_network: list[str]
) -> None:
    cfg = ClientConfig(mode="local")
    d, env = _daemon(tmp_path, cfg, pcm=b"\x01\x00" * (FRAME // 2) * 5)
    task = asyncio.create_task(d.run())
    st = await _until_up(env, task)
    sid = st["session"]

    async def five() -> bool:
        return (await _status(env))["unacked_ms"] >= 500

    await _until(five)
    task.cancel()  # crash: no stop, so the session is not marked ended
    await asyncio.gather(task, return_exceptions=True)
    assert find_resumable(state_dir(env)) == sid

    d2, _ = _daemon(
        tmp_path, cfg, pcm=b"\x02\x00" * (FRAME // 2) * 2, opts=SessionOptions(resume=True)
    )
    task2 = asyncio.create_task(d2.run())
    st2 = await _until_up(env, task2)
    assert st2["session"] == sid

    async def seven() -> bool:
        return (await _status(env))["unacked_ms"] >= 700

    await _until(seven)
    await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "stop"}, timeout=30)
    await asyncio.wait_for(task2, 10)
    frames = Outbox.reopen(sid, state_dir(env) / "sessions").unacked("mic")
    assert [f.seq for f in frames] == list(range(len(frames)))
    assert all(b.t_ms >= a.t_ms + 100 for a, b in zip(frames, frames[1:], strict=False))


async def test_ended_sessions_are_not_resumable(tmp_path: Path, no_network: list[str]) -> None:
    cfg = ClientConfig(mode="local")
    d, env = _daemon(tmp_path, cfg)
    task = asyncio.create_task(d.run())
    await _until_up(env, task)
    await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "stop"}, timeout=30)
    await asyncio.wait_for(task, 10)
    assert find_resumable(state_dir(env)) is None


async def test_roll_starts_a_new_session_after_the_maximum_length(
    tmp_path: Path, no_network: list[str]
) -> None:
    cfg = ClientConfig(mode="local")
    d, env = _daemon(tmp_path, cfg, opts=SessionOptions(roll=True), max_session_s=1.0)
    task = asyncio.create_task(d.run())
    first = (await _until_up(env, task))["session"]

    async def rolled() -> bool:
        try:
            return (await _status(env))["session"] != first
        except (OSError, control.ControlError):
            return False

    await _until(rolled)
    await control.request(runtime_dir(env) / "ctl.sock", {"cmd": "stop"}, timeout=30)
    await asyncio.wait_for(task, 10)
    metas = [
        json.loads(p.read_text()) for p in (state_dir(env) / "sessions").glob("*/session.json")
    ]
    assert len(metas) >= 2 and all(m["ended"] for m in metas)
