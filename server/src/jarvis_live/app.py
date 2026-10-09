import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from jarvis_live.api import sessions, stream
from jarvis_live.auth import TicketStore
from jarvis_live.bus import Bus
from jarvis_live.config import Settings, get_settings
from jarvis_live.db.session import make_engine, make_sessionmaker
from jarvis_live.ingest.hub import IngestHub
from jarvis_live.retention import run_retention_loop
from jarvis_live.stt.backend import STTBackend, WhisperBackend
from jarvis_live.stt.tiers import TierPool, parse_tiers


def create_app(settings: Settings | None = None, *, stt: STTBackend | None = None) -> FastAPI:
    """``stt`` overrides the Whisper backend built from ``settings.whisper_tiers`` (tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or get_settings()
        engine = make_engine(cfg.database_url)
        sm = make_sessionmaker(engine)
        bus = Bus()
        tasks: list[asyncio.Task[None]] = []
        client: httpx.AsyncClient | None = None
        backend = stt
        if backend is None:
            client = httpx.AsyncClient()
            pool = TierPool(
                parse_tiers(cfg.whisper_tiers),
                client,
                probe_interval_s=cfg.tier_probe_interval_s,
                request_timeout_s=cfg.stt_timeout_s,
            )
            backend = WhisperBackend(pool)
            tasks.append(asyncio.create_task(pool.run_probe_loop()))
        hub = IngestHub(cfg, sm, bus, backend)
        app.state.settings = cfg
        app.state.sessionmaker = sm
        app.state.bus = bus
        app.state.tickets = TicketStore(ttl_s=60)
        app.state.hub = hub
        tasks.append(
            asyncio.create_task(
                run_retention_loop(
                    sm, cfg.data_dir, cfg.audio_retention_days, cfg.retention_interval_s
                )
            )
        )
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await hub.shutdown()
            if client is not None:
                await client.aclose()
            await engine.dispose()

    app = FastAPI(title="Jarvis Live", lifespan=lifespan)
    app.include_router(sessions.router)
    app.include_router(stream.router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        """Readiness: always ok until dependencies (DB, STT) are wired in."""
        return {"status": "ok"}

    return app


app = create_app()
