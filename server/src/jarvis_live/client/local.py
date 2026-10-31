"""The local-only transport: the same surface as ``ProducerConnection``, with no network.

Local-only is enforced by construction. The daemon is given this object instead of a
``ProducerConnection``, so there is no code path from capture to the network to forget to check.
Frames stay in the outbox on disk and are never acked or sent."""

import asyncio

from jarvis_live.client.outbox import Outbox
from jarvis_live.protocol import ServerMessage


class NullTransport:
    state = "local"
    failure: str | None = None

    def __init__(self, outbox: Outbox) -> None:
        self.outbox = outbox
        self.events: asyncio.Queue[ServerMessage] = asyncio.Queue()
        self._closed = asyncio.Event()

    def start(self) -> None:
        pass

    def kick(self) -> None:
        pass

    async def end(self, timeout: float = 30.0) -> bool:
        self._closed.set()
        return True

    async def stop(self) -> None:
        self._closed.set()

    async def wait_closed(self) -> None:
        """Never completes on its own: nothing can fail when nothing is connected."""
        await self._closed.wait()
