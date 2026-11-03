"""The daemon's control socket: what happens when one side misbehaves."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis_live.client import control
from jarvis_live.client.control import ControlError, ControlServer


async def _raw(path: Path, line: bytes) -> bytes:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(line)
    await writer.drain()
    reply = await reader.readline()
    writer.close()
    return reply


async def _server(path: Path, handler: control.Handler) -> ControlServer:
    server = ControlServer(path, handler)
    await server.start()
    return server


async def _echo(msg: dict[str, Any], send: control.Send) -> None:
    await send({"got": msg["cmd"]})


async def test_the_socket_is_private_to_its_owner(tmp_path: Path) -> None:
    sock = tmp_path / "ctl.sock"
    server = await _server(sock, _echo)
    assert sock.stat().st_mode & 0o777 == 0o600
    assert await control.request(sock, {"cmd": "ping"}) == {"got": "ping"}
    await server.close()
    assert not sock.exists()


@pytest.mark.parametrize("line", [b"not json\n", b"[1]\n", b'{"no": "cmd"}\n'])
async def test_a_bad_request_gets_an_error_not_a_crash(tmp_path: Path, line: bytes) -> None:
    sock = tmp_path / "ctl.sock"
    server = await _server(sock, _echo)
    assert "bad request" in json.loads(await _raw(sock, line))["error"]
    assert await control.request(sock, {"cmd": "still-alive"}) == {"got": "still-alive"}
    await server.close()


async def test_a_handler_failure_reaches_the_client_as_an_error(tmp_path: Path) -> None:
    async def boom(msg: dict[str, Any], send: control.Send) -> None:
        raise RuntimeError("disk full")

    sock = tmp_path / "ctl.sock"
    server = await _server(sock, boom)
    with pytest.raises(ControlError, match="disk full"):
        await control.request(sock, {"cmd": "stop"})
    await server.close()


async def test_a_daemon_that_closes_without_replying_is_an_error(tmp_path: Path) -> None:
    async def silent(msg: dict[str, Any], send: control.Send) -> None:
        return None

    sock = tmp_path / "ctl.sock"
    server = await _server(sock, silent)
    with pytest.raises(ControlError, match="without replying"):
        await control.request(sock, {"cmd": "status"})
    await server.close()


async def test_a_daemon_that_hangs_times_out(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def hang(msg: dict[str, Any], send: control.Send) -> None:
        started.set()
        await asyncio.Event().wait()

    sock = tmp_path / "ctl.sock"
    server = await _server(sock, hang)
    with pytest.raises(ControlError, match="did not answer"):
        await control.request(sock, {"cmd": "status"}, timeout=0.1)
    assert started.is_set()
    await server.close()


async def test_closing_drops_a_connection_whose_handler_will_not_finish(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def stubborn(msg: dict[str, Any], send: control.Send) -> None:
        started.set()
        await asyncio.Event().wait()

    sock = tmp_path / "ctl.sock"
    server = await _server(sock, stubborn)
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(b'{"cmd":"subscribe"}\n')
    await writer.drain()
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(server.close(grace=0.05), 5)
    assert await asyncio.wait_for(reader.read(), 5) == b""  # the client sees the socket close
    assert not sock.exists()
    writer.close()


async def test_a_client_that_connects_and_says_nothing_is_ignored(tmp_path: Path) -> None:
    sock = tmp_path / "ctl.sock"
    server = await _server(sock, _echo)
    _, writer = await asyncio.open_unix_connection(str(sock))
    writer.close()
    await writer.wait_closed()
    assert await control.request(sock, {"cmd": "ping"}) == {"got": "ping"}
    await server.close()


async def test_a_client_that_leaves_cancels_a_subscription(tmp_path: Path) -> None:
    gone = asyncio.Event()

    async def feed(msg: dict[str, Any], send: control.Send) -> None:
        try:
            await send({"n": 1})
            await asyncio.Event().wait()
        finally:
            gone.set()

    sock = tmp_path / "ctl.sock"
    server = await _server(sock, feed)
    events = control.subscribe(sock)
    assert await anext(events) == {"n": 1}
    await events.aclose()
    await asyncio.wait_for(gone.wait(), 5)
    await server.close()
