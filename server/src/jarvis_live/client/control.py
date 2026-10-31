"""JSON-lines control protocol over a unix socket between ``capture`` commands and the daemon.

Local IPC only: never a TCP listener. Requests are one JSON object per line with a ``cmd``; the
reply is one JSON object per line (``{"error": ...}`` on failure). ``subscribe`` keeps the
connection open and streams one JSON event per line."""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

Send = Callable[[dict[str, Any]], Awaitable[None]]
Handler = Callable[[dict[str, Any], Send], Coroutine[Any, Any, None]]


class ControlError(Exception):
    pass


async def request(path: Path, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(str(path))
        try:
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if not line:
            raise ControlError("daemon closed the connection without replying")
        reply: dict[str, Any] = json.loads(line)
        if "error" in reply:
            raise ControlError(str(reply["error"]))
        return reply

    try:
        return await asyncio.wait_for(go(), timeout)
    except TimeoutError as e:
        raise ControlError("daemon did not answer in time") from e


async def subscribe(path: Path) -> AsyncIterator[dict[str, Any]]:
    """Yields events from the daemon until it closes the connection."""
    reader, writer = await asyncio.open_unix_connection(str(path))
    try:
        writer.write(b'{"cmd":"subscribe"}\n')
        await writer.drain()
        while line := await reader.readline():
            yield json.loads(line)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


class ControlServer:
    def __init__(self, path: Path, handler: Handler) -> None:
        self.path = path
        self._handler = handler
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._serve, path=str(self.path))
        self.path.chmod(0o600)

    async def close(self, grace: float = 2.0) -> None:
        if self._server is not None:
            self._server.close()
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=grace)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)

        async def send(obj: dict[str, Any]) -> None:
            writer.write(json.dumps(obj).encode() + b"\n")
            await writer.drain()

        try:
            line = await reader.readline()
            if not line:
                return
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict) or "cmd" not in msg:
                    raise ValueError("expected an object with a cmd")
            except ValueError as e:
                await send({"error": f"bad request: {e}"})
                return
            # A client that goes away (EOF) cancels a long-running handler such as subscribe.
            watcher = asyncio.create_task(reader.read())
            handler: asyncio.Task[None] = asyncio.create_task(self._handler(msg, send))
            await asyncio.wait({watcher, handler}, return_when=asyncio.FIRST_COMPLETED)
            for t in (watcher, handler):
                if not t.done():
                    t.cancel()
            results = await asyncio.gather(watcher, handler, return_exceptions=True)
            if isinstance(results[1], Exception) and not isinstance(results[1], ConnectionError):
                with contextlib.suppress(Exception):
                    await send({"error": str(results[1])})
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self._tasks.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
