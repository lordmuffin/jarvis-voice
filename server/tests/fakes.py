"""Shared test doubles and DB seeding for the A3 tests."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.db.models import SegmentRow
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.llm.client import LLMError

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMCall:
    model: str
    system: str
    user: str
    schema: str


@dataclass
class FakeLLM:
    """Returns queued responses per schema name (``CopilotDelta`` / ``FinalNote``). A response
    may be a model, an ``LLMError`` to raise, or a callable producing either. The last queued
    response repeats once the queue is down to one."""

    responses: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[LLMCall] = field(default_factory=list)
    delay_s: float = 0.0
    active: int = 0
    max_active: int = 0

    def queue(self, schema: str, *items: Any) -> "FakeLLM":
        self.responses.setdefault(schema, []).extend(items)
        return self

    async def complete_json(
        self, *, model: str, system: str, user: str, schema: type[T], timeout_s: float | None = None
    ) -> T:
        self.calls.append(LLMCall(model, system, user, schema.__name__))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            q = self.responses.get(schema.__name__)
            if not q:
                raise LLMError(f"no canned {schema.__name__} response")
            item = q.pop(0) if len(q) > 1 else q[0]
            if callable(item):
                item = item()
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, schema)
            return item
        finally:
            self.active -= 1


class FakeClock:
    """Manual clock: ``sleep`` blocks until ``advance`` moves time past its deadline."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self.now + seconds, fut))
        await fut

    async def advance(self, seconds: float) -> None:
        self.now += seconds
        due = [w for w in self._waiters if w[0] <= self.now]
        self._waiters = [w for w in self._waiters if w[0] > self.now]
        for _, fut in due:
            if not fut.done():
                fut.set_result(None)
        for _ in range(10):  # let woken tasks run
            await asyncio.sleep(0)


@dataclass
class Sent:
    title: str
    message: str
    priority: int
    url: str | None


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[Sent] = []

    async def notify(self, title: str, message: str, priority: int, url: str | None = None) -> None:
        self.sent.append(Sent(title, message, priority, url))


async def make_session(
    sm: "async_sessionmaker[AsyncSession]",
    device_id: uuid.UUID,
    *,
    status: str = "live",
    mode: str = "meeting",
    local_only: bool = False,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> uuid.UUID:
    async with sm() as db:
        row = SessionRow(
            device_id=device_id,
            title=None,
            mode=mode,
            status=status,
            channels=["mic", "system"],
            local_only=local_only,
            started_at=started_at or datetime(2026, 10, 9, 14, 30, tzinfo=UTC),
            ended_at=ended_at,
        )
        db.add(row)
        await db.commit()
        return row.id


async def add_segments(
    sm: "async_sessionmaker[AsyncSession]",
    session_id: uuid.UUID,
    rows: list[tuple[int, int, str, str]],
    source: str = "final",
) -> None:
    """``rows`` are ``(start_ms, end_ms, speaker, text)``."""
    async with sm() as db:
        for start, end, speaker, text in rows:
            db.add(
                SegmentRow(
                    session_id=session_id,
                    channel="mic" if speaker == "me" else "system",
                    speaker=speaker,
                    start_ms=start,
                    end_ms=end,
                    text=text,
                    source=source,
                    stt_tier="fake" if source == "final" else None,
                )
            )
        await db.commit()
