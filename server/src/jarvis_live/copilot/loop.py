"""Per-session copilot: watches for new final segments, asks the LLM for a delta, publishes a
full snapshot."""

import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.prompts import COPILOT_SYSTEM, copilot_user
from jarvis_live.copilot.schema import CopilotDelta
from jarvis_live.copilot.state import CopilotState
from jarvis_live.copilot.transcript import load_lines, render_lines
from jarvis_live.db.models import CopilotItem, SegmentRow
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.llm.client import LLM, LLMError
from jarvis_live.logging_setup import session_logger
from jarvis_live.protocol import Copilot
from jarvis_live.vault.index import VaultContext

SNAPSHOT = "snapshot"
RELATED = "related"


class Clock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class CopilotLoop:
    """One instance per live session; ``run()`` is its single task.

    A cycle runs when at least ``copilot_min_segments`` new final segments have arrived, or
    ``copilot_max_wait_s`` have passed since the first of ≥1 new ones. Cycles are awaited inline
    in ``tick()``, so they can never overlap."""

    def __init__(
        self,
        session_id: uuid.UUID,
        *,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        llm: LLM,
        vault: VaultContext,
        stream_ms: Callable[[], int],
        set_llm_ok: Callable[[bool], None] = lambda ok: None,
        clock: Clock | None = None,
    ) -> None:
        self.session_id = session_id
        self._cfg = settings
        self._sm = sessionmaker
        self._bus = bus
        self._llm = llm
        self._vault = vault
        self._stream_ms = stream_ms
        self._set_llm_ok = set_llm_ok
        self._clock = clock or RealClock()
        self._log = session_logger(__name__, session_id)
        self.state = CopilotState()
        self._seen = 0  # final segments already accounted for by a cycle
        self._pending_since: float | None = None
        self._llm_ok = True
        self._restored = False

    async def run(self) -> None:
        await self._restore()
        while True:
            await self._clock.sleep(self._cfg.copilot_poll_s)
            try:
                await self.tick()
            except Exception:
                self._log.exception("copilot tick failed")

    async def tick(self) -> None:
        """One scheduling decision. Public so tests can drive it with a fake clock."""
        if not self._restored:
            await self._restore()
        changed = self.state.expire(self._now_ms())
        total = await self._final_count()
        new = total - self._seen
        now = self._clock.monotonic()
        if new > 0 and self._pending_since is None:
            self._pending_since = now
        due = new >= self._cfg.copilot_min_segments or (
            new >= 1
            and self._pending_since is not None
            and now - self._pending_since >= self._cfg.copilot_max_wait_s
        )
        if due:
            await self._cycle(total)
        elif changed:
            await self._publish()

    # --- one cycle --------------------------------------------------------------------------

    async def _cycle(self, total: int) -> None:
        self._seen, self._pending_since = total, None
        now_ms = self._now_ms()
        async with self._sm() as db:
            lines = await load_lines(
                db, self.session_id, max(0, now_ms - int(self._cfg.copilot_window_s * 1000))
            )
            title = (
                await db.execute(select(SessionRow.title).where(SessionRow.id == self.session_id))
            ).scalar_one_or_none()
        user = copilot_user(
            today=datetime.now(UTC).date().isoformat(),
            title=title,
            state_json=self.state.prompt_json(),
            related="\n".join(f"- {r.title}: {r.snippet}" for r in self.state.related),
            transcript=render_lines(lines),
        )
        try:
            delta = await self._llm.complete_json(
                model=self._cfg.copilot_model,
                system=COPILOT_SYSTEM,
                user=user,
                schema=CopilotDelta,
                timeout_s=self._cfg.copilot_timeout_s,
            )
        except LLMError as e:
            self._log.warning("copilot cycle skipped: %s", e)
            self._mark_llm(False)
            return
        self._mark_llm(True)

        if title is None and delta.title:
            await self._name_session(delta.title)
        changed = self.state.apply(delta, now_ms, self._cfg.suggestion_default_ttl_s)
        if delta.topics:
            hits = await self._vault.search(
                delta.topics, self.state.shown, self._cfg.copilot_max_related
            )
            if hits:
                await self._persist(RELATED, {"hits": [h.model_dump() for h in hits]})
                changed = self.state.set_related(hits) or changed
        if changed:
            await self._publish()

    async def _name_session(self, title: str) -> None:
        """Auto-name an untitled session. Never overwrites a title, including one the user set
        while this cycle was in flight."""
        async with self._sm() as db:
            result = await db.execute(
                update(SessionRow)
                .where(SessionRow.id == self.session_id, SessionRow.title.is_(None))
                .values(title=title)
            )
            await db.commit()
        if result.rowcount:  # type: ignore[attr-defined]
            self._log.info("session auto-named")

    def _mark_llm(self, ok: bool) -> None:
        if ok != self._llm_ok:
            self._llm_ok = ok
            self._set_llm_ok(ok)

    # --- persistence ------------------------------------------------------------------------

    async def _publish(self) -> None:
        snap = self.state.snapshot()
        await self._persist(SNAPSHOT, snap.model_dump())
        self._bus.publish(str(self.session_id), snap)

    async def _persist(self, kind: str, payload: dict[str, object]) -> None:
        async with self._sm() as db:
            db.add(CopilotItem(session_id=self.session_id, kind=kind, payload=payload))
            await db.commit()

    async def _restore(self) -> None:
        """Pick up where a previous process left off (restart mid-session)."""
        self._restored = True
        async with self._sm() as db:
            latest = (
                await db.execute(
                    select(CopilotItem.payload)
                    .where(CopilotItem.session_id == self.session_id, CopilotItem.kind == SNAPSHOT)
                    .order_by(CopilotItem.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            shown_rows = (
                await db.execute(
                    select(CopilotItem.payload).where(
                        CopilotItem.session_id == self.session_id, CopilotItem.kind == RELATED
                    )
                )
            ).scalars()
            shown = {h["path"] for p in shown_rows for h in p["hits"]}
        if latest is not None:
            self.state = CopilotState.from_snapshot(Copilot.model_validate(latest), shown)
        else:
            self.state.shown |= shown
        # Segments finalized before a restart were already considered by earlier cycles.
        self._seen = await self._final_count()

    # --- helpers ----------------------------------------------------------------------------

    async def _final_count(self) -> int:
        async with self._sm() as db:
            return (
                await db.execute(
                    select(func.count())
                    .select_from(SegmentRow)
                    .where(SegmentRow.session_id == self.session_id, SegmentRow.source == "final")
                )
            ).scalar_one()

    def _now_ms(self) -> int:
        return self._stream_ms()


__all__ = ["Clock", "CopilotLoop", "RealClock"]
