"""The ask desk records how a reply was played in the order the client said it."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

from jarvis_live.config import Settings
from jarvis_live.protocol import AskPlayed
from jarvis_live.voice.desk import AskDesk


class SlowFirstSessions:
    """A sessionmaker whose first session takes its row lock late, so a later write can go first."""

    def __init__(self, row: SimpleNamespace) -> None:
        self.row = row
        self.opened = 0

    def __call__(self) -> "SlowFirstSessions._Session":
        self.opened += 1
        return self._Session(self.row, delay=0.05 if self.opened == 1 else 0.0)

    class _Session:
        def __init__(self, row: SimpleNamespace, delay: float) -> None:
            self.row, self.delay = row, delay

        async def __aenter__(self) -> "SlowFirstSessions._Session":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def begin(self) -> "SlowFirstSessions._Session":
            return self

        async def get(self, _model: object, _row_id: int, **_kw: Any) -> SimpleNamespace:
            await asyncio.sleep(self.delay)
            return self.row


def desk(sessions: SlowFirstSessions) -> AskDesk:
    d = AskDesk(
        uuid.uuid4(),
        uuid.uuid4(),
        settings=Settings(),
        sessionmaker=sessions,  # type: ignore[arg-type]
        bus=None,  # type: ignore[arg-type]
        runner=None,  # type: ignore[arg-type]
        queue=None,
        position_ms=lambda: 0,
    )
    d._replies["a1"] = (7, "Priya runs it.")
    return d


async def test_done_after_started_stays_done_when_the_first_write_is_slow() -> None:
    row = SimpleNamespace(payload={"outcome": "answer"})
    d = desk(SlowFirstSessions(row))
    d.played(AskPlayed(id="a1", state="started", t_ms=10, latency_ms=2900))
    d.played(AskPlayed(id="a1", state="done", t_ms=1510))
    await d.close()
    assert row.payload == {"outcome": "answer", "played": "done", "latency_ms": 2900}


async def test_a_skip_after_started_is_what_stays() -> None:
    row = SimpleNamespace(payload={"outcome": "answer"})
    d = desk(SlowFirstSessions(row))
    d.played(AskPlayed(id="a1", state="started", t_ms=10, latency_ms=1200))
    d.played(AskPlayed(id="a1", state="skipped", t_ms=20, reason="interrupted"))
    await d.close()
    assert row.payload["played"] == "skipped"
    assert row.payload["skipped"] == "interrupted"
