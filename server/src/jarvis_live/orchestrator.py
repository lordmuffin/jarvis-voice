"""Wires the A3 services to session lifecycle events: copilot loop per live session, finalizer
on end, idle-session ending, and the STT-outage alert."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.loop import Clock, CopilotLoop, RealClock
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.finalizer import Finalizer
from jarvis_live.ingest.hub import IngestHub, SessionIngest
from jarvis_live.llm.client import LLM
from jarvis_live.notify.gotify import Notifier
from jarvis_live.stt.backend import STTBackend
from jarvis_live.vault.index import VaultContext

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        bus: Bus,
        stt: STTBackend,
        llm: LLM,
        vault: VaultContext,
        notifier: Notifier,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._stt = stt
        self._notifier = notifier
        self._clock = clock or RealClock()
        self._bus, self._llm, self._vault = bus, llm, vault
        self._finalizer = Finalizer(settings, sessionmaker, bus, llm, vault, notifier)
        self._hub: IngestHub | None = None
        self._copilots: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._finals: set[asyncio.Task[object]] = set()
        self._orphans: dict[uuid.UUID, float] = {}  # live in the DB, no ingest state: first seen
        self._outage_since: float | None = None
        self._outage_notified = False

    def attach(self, hub: IngestHub) -> None:
        self._hub = hub

    # --- lifecycle hooks (called by the hub; must not block) ---------------------------------

    def on_start(self, state: SessionIngest) -> None:
        self._copilots[state.session_id] = asyncio.create_task(self._run_copilot(state))

    def on_end(self, session_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._end(session_id))
        self._finals.add(task)
        task.add_done_callback(self._finals.discard)

    async def _run_copilot(self, state: SessionIngest) -> None:
        async with self._sm() as db:
            row = await db.get(SessionRow, state.session_id)
        if row is None or row.local_only:
            return  # local-only sessions are never sent to an LLM
        pipeline = state.pipeline

        def set_llm_ok(ok: bool) -> None:
            pipeline.llm_ok = ok
            pipeline.publish_status()

        pipeline.llm_ok = True  # healthy until a cycle fails
        loop = CopilotLoop(
            state.session_id,
            settings=self._cfg,
            sessionmaker=self._sm,
            bus=self._bus,
            llm=self._llm,
            vault=self._vault,
            stream_ms=pipeline.position_ms,
            set_llm_ok=set_llm_ok,
            clock=self._clock,
        )
        await loop.run()

    async def _end(self, session_id: uuid.UUID) -> None:
        if (task := self._copilots.pop(session_id, None)) is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._finalizer.run(session_id)

    async def recover(self) -> None:
        """Finalize recent sessions left in ``finalizing`` by a previous process."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._cfg.finalize_recover_max_age_s)
        async with self._sm() as db:
            ids = (
                (
                    await db.execute(
                        select(SessionRow.id).where(
                            SessionRow.status == "finalizing", SessionRow.ended_at >= cutoff
                        )
                    )
                )
                .scalars()
                .all()
            )
        for sid in ids:
            log.info("recovering unfinalized session %s", sid)
            self.on_end(sid)

    # --- watchdog ---------------------------------------------------------------------------

    async def run_watchdog(self, interval_s: float = 10.0) -> None:
        while True:
            await self._clock.sleep(interval_s)
            try:
                await self.check_once()
            except Exception:
                log.exception("watchdog check failed")

    async def check_once(self) -> None:
        assert self._hub is not None
        now = self._clock.monotonic()
        live = self._hub.live_sessions()
        await self._check_stt_outage(now, bool(live))
        for st in live:
            if now - st.pipeline.last_audio_at > self._cfg.idle_end_s:
                log.info("ending idle session %s", st.session_id)
                await self._hub.end(st.session_id)
        await self._end_orphans(now, {st.session_id for st in live})

    async def _end_orphans(self, now: float, in_hub: set[uuid.UUID]) -> None:
        """Sessions that are ``live`` in the DB but have no ingest state (e.g. after a restart,
        or a producer that never connected) end after the same idle period."""
        assert self._hub is not None
        async with self._sm() as db:
            ids = set(
                (await db.execute(select(SessionRow.id).where(SessionRow.status == "live")))
                .scalars()
                .all()
            )
        orphans = ids - in_hub
        self._orphans = {sid: self._orphans.get(sid, now) for sid in orphans}
        for sid, first_seen in list(self._orphans.items()):
            if now - first_seen > self._cfg.idle_end_s:
                del self._orphans[sid]
                log.info("ending idle session %s (no producer)", sid)
                await self._hub.end(sid)

    async def _check_stt_outage(self, now: float, any_live: bool) -> None:
        if not any_live or self._stt.current_tier() is not None:
            self._outage_since, self._outage_notified = None, False
            return
        if self._outage_since is None:
            self._outage_since = now
        if not self._outage_notified and now - self._outage_since > self._cfg.stt_outage_notify_s:
            self._outage_notified = True  # once per outage
            await self._notifier.notify(
                "Jarvis: transcription down",
                "All speech-to-text tiers have been unhealthy for over "
                f"{int(self._cfg.stt_outage_notify_s // 60)} minutes during a live session.",
                8,
            )

    # --- shutdown ---------------------------------------------------------------------------

    async def shutdown(self) -> None:
        tasks = [*self._copilots.values(), *self._finals]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._copilots.clear()
        self._finals.clear()
