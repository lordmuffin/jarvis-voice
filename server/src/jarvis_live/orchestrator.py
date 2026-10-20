"""Wires the A3 services to session lifecycle events: copilot loop (and ideas loop) per live
session, a last ideas pass then the finalizer on end, idle-session ending, and the STT-outage
alert."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.loop import Clock, CopilotLoop, RealClock
from jarvis_live.db.models import OWNER_USER_ID, User
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.finalize.finalizer import Finalizer
from jarvis_live.ingest.hub import IngestHub, SessionIngest
from jarvis_live.llm.client import LLM
from jarvis_live.notify.gotify import Notifier
from jarvis_live.stt.backend import STTBackend
from jarvis_live.vault.index import NullVault, VaultContext
from jarvis_live.workitems.ideas import IdeasLoop, IdeasPass

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
        on_final: Callable[[uuid.UUID], Awaitable[None]] | None = None,
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
        self._ideas: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._ideas_pass = (
            IdeasPass(settings, sessionmaker, llm) if settings.work_items_enabled else None
        )
        self._finals: set[asyncio.Task[object]] = set()
        self._finalizing: set[uuid.UUID] = set()  # a finalizer run is in flight
        self._on_final = on_final  # after a note is written (auto-publish)
        self._orphans: dict[uuid.UUID, float] = {}  # live in the DB, no ingest state: first seen
        self._outage_since: float | None = None
        self._outage_notified = False

    def attach(self, hub: IngestHub) -> None:
        self._hub = hub

    # --- lifecycle hooks (called by the hub; must not block) ---------------------------------

    def on_start(self, state: SessionIngest) -> None:
        self._copilots[state.session_id] = asyncio.create_task(self._run_copilot(state))

    def on_end(self, session_id: uuid.UUID) -> None:
        self._track(session_id, self._end(session_id))

    def regenerate(self, session_id: uuid.UUID, replace: str | None) -> None:
        """Re-run the finalizer on a session the caller has put back into ``finalizing``.
        ``replace`` is its current note, removed if the new one gets another name."""
        self._track(session_id, self._finalize(session_id, replace=replace, notify=False))

    def is_finalizing(self, session_id: uuid.UUID) -> bool:
        return session_id in self._finalizing

    def _track(self, session_id: uuid.UUID, coro: Awaitable[None]) -> None:
        self._finalizing.add(session_id)

        async def run() -> None:
            try:
                await coro
            finally:
                self._finalizing.discard(session_id)

        task = asyncio.create_task(run())
        self._finals.add(task)
        task.add_done_callback(self._finals.discard)

    async def _run_copilot(self, state: SessionIngest) -> None:
        async with self._sm() as db:
            row = await db.get(SessionRow, state.session_id)
        if row is None or row.local_only:
            return  # local-only sessions are never sent to an LLM
        if self._ideas_pass is not None and await self._ideas_eligible(row):
            ideas = IdeasLoop(
                state.session_id,
                settings=self._cfg,
                sessionmaker=self._sm,
                runner=self._ideas_pass,
                clock=self._clock,
            )
            self._ideas[state.session_id] = asyncio.create_task(ideas.run())
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
            # The vault is the owner's own Obsidian vault: never shown in other users' sessions.
            vault=self._vault if row.user_id == OWNER_USER_ID else NullVault(),
            stream_ms=pipeline.position_ms,
            set_llm_ok=set_llm_ok,
            clock=self._clock,
        )
        await loop.run()

    async def _ideas_eligible(self, row: SessionRow) -> bool:
        """Work items target the owner's repositories: only the owner's and admins' sessions
        are mined for them, and never a local-only one."""
        if row.local_only:
            return False
        if row.user_id == OWNER_USER_ID:
            return True
        async with self._sm() as db:
            user = await db.get(User, row.user_id)
        return user is not None and user.is_admin

    async def _end(self, session_id: uuid.UUID) -> None:
        for tasks in (self._copilots, self._ideas):
            if (task := tasks.pop(session_id, None)) is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await self._flush_ideas(session_id)
        await self._finalize(session_id)

    async def _flush_ideas(self, session_id: uuid.UUID) -> None:
        """One last ideas pass, so what was said in the final minutes (or in a session the loop
        never ran for) is in the note. Its failure never blocks the note."""
        if self._ideas_pass is None:
            return
        async with self._sm() as db:
            row = await db.get(SessionRow, session_id)
        if row is None or row.status != "finalizing" or not await self._ideas_eligible(row):
            return
        try:
            await self._ideas_pass.run(session_id)
        except Exception:
            log.exception("final ideas pass failed for %s", session_id)

    async def _finalize(
        self, session_id: uuid.UUID, *, replace: str | None = None, notify: bool = True
    ) -> None:
        result = await self._finalizer.run(session_id, replace=replace, notify=notify)
        if result is None or self._on_final is None:
            return
        try:
            await self._on_final(session_id)
        except Exception:  # publishing trouble never fails a finished note
            log.exception("after-note hook failed for %s", session_id)

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
            if not st.uploading and now - st.pipeline.last_audio_at > self._cfg.idle_end_s:
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
        tasks = [*self._copilots.values(), *self._ideas.values(), *self._finals]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._copilots.clear()
        self._ideas.clear()
        self._finals.clear()
