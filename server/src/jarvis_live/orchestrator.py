"""Wires the A3 services to session lifecycle events: copilot loop (and ideas loop) per live
session, a last ideas pass then the finalizer on end, idle-session ending, and the STT-outage
alert."""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live import metrics
from jarvis_live.actions.intake import ActionIntake, SessionProposals
from jarvis_live.bus import Bus
from jarvis_live.config import Settings
from jarvis_live.copilot.loop import Clock, CopilotLoop, RealClock
from jarvis_live.db.models import OWNER_USER_ID, User
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.diarize.client import Diarizer
from jarvis_live.diarize.service import DiarizeService
from jarvis_live.finalize.finalizer import Finalizer
from jarvis_live.ingest.hub import IngestHub, SessionIngest
from jarvis_live.ingest.retranscribe import RetranscribeFailed, retranscribe
from jarvis_live.llm.client import LLM
from jarvis_live.notify.gotify import Notifier
from jarvis_live.processing import Job, JobSource, Stage
from jarvis_live.slo import SloMonitor
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
        intake: ActionIntake | None = None,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._stt = stt
        self._notifier = notifier
        self._clock = clock or RealClock()
        self._bus, self._llm, self._vault = bus, llm, vault
        self._intake = intake
        self._finalizer = Finalizer(settings, sessionmaker, bus, llm, vault, notifier, intake)
        self._hub: IngestHub | None = None
        self._copilots: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._ideas: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._ideas_pass = (
            IdeasPass(settings, sessionmaker, llm) if settings.work_items_enabled else None
        )
        self._finals: set[asyncio.Task[object]] = set()
        self._job_t0: dict[uuid.UUID, float] = {}  # monotonic start of each job
        self._jobs: dict[uuid.UUID, Job] = {}  # a finalizer (or reprocess) run is in flight
        self._transcription_failed: set[uuid.UUID] = set()  # the last reprocess could not
        self._on_final = on_final  # after a note is written (auto-publish)
        self._orphans: dict[uuid.UUID, float] = {}  # live in the DB, no ingest state: first seen
        self._slo = SloMonitor(settings, notifier, self._clock)
        metrics.add_observer(self._slo.observe)
        self._outage_since: float | None = None
        self._outage_notified = False

    def attach(self, hub: IngestHub) -> None:
        self._hub = hub

    # --- lifecycle hooks (called by the hub; must not block) ---------------------------------

    def on_start(self, state: SessionIngest) -> None:
        self._copilots[state.session_id] = asyncio.create_task(self._run_copilot(state))

    def on_end(self, session_id: uuid.UUID) -> None:
        self._track(session_id, "finalize", "summary", self._end(session_id))

    def regenerate(self, session_id: uuid.UUID, replace: str | None) -> None:
        """Re-run the finalizer on a session the caller has put back into ``finalizing``.
        ``replace`` is its current note, removed if the new one gets another name."""
        self._track(
            session_id,
            "regenerate",
            "summary",
            self._finalize(session_id, replace=replace, notify=False),
        )

    def reprocess(self, session_id: uuid.UUID, replace: str | None, *, restore: str) -> None:
        """Transcribe a session the caller has put back into ``finalizing`` again from its stored
        audio, then re-run the finalizer. If the audio cannot be transcribed, the transcript and
        note stay as they were and the session returns to ``restore``."""
        self._transcription_failed.discard(session_id)
        self._track(
            session_id,
            "reprocess",
            "transcription",
            self._reprocess(session_id, replace, restore),
        )

    def is_finalizing(self, session_id: uuid.UUID) -> bool:
        return session_id in self._jobs

    def job(self, session_id: uuid.UUID) -> Job | None:
        return self._jobs.get(session_id)

    def progress(self, session_id: uuid.UUID) -> float | None:
        """How much of the recording a reprocess has transcribed (0-1), while it does."""
        job = self._jobs.get(session_id)
        if job is None or job.stage != "transcription" or job.progress is None:
            return None
        return job.progress()

    def transcription_failed(self, session_id: uuid.UUID) -> bool:
        return session_id in self._transcription_failed

    def _track(
        self, session_id: uuid.UUID, source: JobSource, stage: Stage, coro: Awaitable[None]
    ) -> None:
        self._jobs[session_id] = Job(source, stage, datetime.now(UTC))
        self._job_t0[session_id] = time.monotonic()

        async def run() -> None:
            try:
                await coro
            finally:
                self._jobs.pop(session_id, None)
                self._job_t0.pop(session_id, None)

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
            proposals=await self._proposals_for(row),
        )
        await loop.run()

    async def _proposals_for(self, row: SessionRow) -> SessionProposals | None:
        """Action proposals are reviewed by the vault's owner: only the owner's and admins'
        sessions get them, and only where the feature is on."""
        if self._intake is None or not self._cfg.actions_enabled:
            return None
        if not await self._ideas_eligible(row):
            return None
        return self._intake.for_session(row.id, row.user_id)

    async def _ideas_eligible(self, row: SessionRow) -> bool:
        """Work items target the owner's repositories: only the owner's and admins' sessions
        are mined for them, and never a local-only or restricted one."""
        if row.local_only or row.restricted:
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
        await self._label_speakers(session_id)
        await self._finalize(session_id)

    async def _label_speakers(self, session_id: uuid.UUID) -> None:
        """Who said what, before the note is written. Never raises, never fails the session."""
        async with self._sm() as db:
            row = await db.get(SessionRow, session_id)
        if row is not None:
            await self._diarize.run(session_id, list(row.channels))

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

    async def _reprocess(self, session_id: uuid.UUID, replace: str | None, restore: str) -> None:
        async with self._sm() as db:
            row = await db.get(SessionRow, session_id)
        if row is None:
            return
        job = self._jobs.get(session_id)

        def watch(probe: Callable[[], float | None]) -> None:
            if job is not None:
                job.progress = probe

        try:
            await retranscribe(
                session_id,
                list(row.channels),
                settings=self._cfg,
                sessionmaker=self._sm,
                bus=self._bus,
                stt=self._stt,
                on_progress=watch,
            )
        except RetranscribeFailed as e:
            log.warning("could not transcribe %s again: %s", session_id, e)
            self._transcription_failed.add(session_id)
            async with self._sm() as db:
                await db.execute(
                    update(SessionRow)
                    .where(SessionRow.id == session_id, SessionRow.status == "finalizing")
                    .values(status=restore)
                )
                await db.commit()
            return
        if job is not None:
            job.stage = "summary"
        await self._diarize.realign(session_id)  # the new segments, from the stored turns
        await self._finalize(session_id, replace=replace, notify=False)

    async def _finalize(
        self, session_id: uuid.UUID, *, replace: str | None = None, notify: bool = True
    ) -> None:
        job = self._jobs.get(session_id)
        result = await self._finalizer.run(
            session_id,
            replace=replace,
            notify=notify,
            source=job.source if job else "finalize",
            since=self._job_t0.get(session_id),
        )
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
        metrics.sessions(len(live), max((st.pipeline.lag_ms() for st in live), default=0))
        await self._check_stt_outage(now, bool(live))
        await self._slo.check(now, any_live=bool(live), stt_up=self._stt.current_tier() is not None)
        for st in live:
            if (
                not st.uploading
                and now - st.pipeline.last_audio_at > self._cfg.idle_end_s
                and not self._producer_may_return(st, now)
            ):
                log.info("ending idle session %s", st.session_id)
                await self._hub.end(st.session_id)
        await self._end_orphans(now, {st.session_id for st in live})

    def _producer_may_return(self, st: SessionIngest, now: float) -> bool:
        """A producer that dropped without `end` keeps the session alive for a grace period."""
        gone = st.producer_gone_at
        return (
            st.producer is None
            and gone is not None
            and now - gone < self._cfg.producer_gone_grace_s
        )

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
        metrics.remove_observer(self._slo.observe)
        tasks = [*self._copilots.values(), *self._ideas.values(), *self._finals]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._copilots.clear()
        self._ideas.clear()
        self._finals.clear()
