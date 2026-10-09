"""In-process pub/sub keyed by session. Callers persist first, then publish."""

import asyncio
import logging
from collections import defaultdict

from pydantic import BaseModel

log = logging.getLogger(__name__)

_CLOSED = object()


class Subscription:
    def __init__(self, bus: "Bus", session_id: str, maxsize: int) -> None:
        self._bus = bus
        self.session_id = session_id
        self._queue: asyncio.Queue[object] = asyncio.Queue(maxsize)

    def put(self, item: BaseModel) -> bool:
        """Enqueue an event for this subscriber only (e.g. a per-connection ack)."""
        return self._enqueue(item)

    def _enqueue(self, item: object) -> bool:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            return False
        return True

    async def get(self) -> BaseModel | None:
        """Next event, or ``None`` once the subscription is closed."""
        item = await self._queue.get()
        if item is _CLOSED:
            self._queue.put_nowait(_CLOSED)  # stay closed for any further get()
            return None
        assert isinstance(item, BaseModel)
        return item

    def close(self) -> None:
        self._bus._remove(self)
        while True:  # make room for the sentinel, waking any waiting getter
            try:
                self._queue.put_nowait(_CLOSED)
                return
            except asyncio.QueueFull:
                self._queue.get_nowait()


class Bus:
    def __init__(self, queue_size: int = 1000) -> None:
        self._queue_size = queue_size
        self._subs: dict[str, set[Subscription]] = defaultdict(set)

    def subscribe(self, session_id: str) -> Subscription:
        sub = Subscription(self, session_id, self._queue_size)
        self._subs[session_id].add(sub)
        return sub

    def publish(self, session_id: str, event: BaseModel) -> None:
        for sub in list(self._subs.get(session_id, ())):
            if not sub.put(event):
                log.warning(
                    "subscriber queue full, dropping event",
                    extra={"session_id": session_id, "event_type": type(event).__name__},
                )

    def subscriber_count(self, session_id: str) -> int:
        return len(self._subs.get(session_id, ()))

    def _remove(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.session_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.session_id]
