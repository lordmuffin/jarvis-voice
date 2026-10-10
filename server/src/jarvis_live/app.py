import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from jarvis_live.api import sessions, stream
from jarvis_live.auth import TicketStore
from jarvis_live.bus import Bus
from jarvis_live.config import Settings, get_settings
from jarvis_live.db.session import make_engine, make_sessionmaker, run_migrations
from jarvis_live.ingest.hub import IngestHub
from jarvis_live.llm.client import LLM, LiteLLMClient
from jarvis_live.notify.gotify import GotifyNotifier, Notifier
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.retention import run_retention_loop
from jarvis_live.stt.backend import STTBackend, WhisperBackend
from jarvis_live.stt.tiers import TierPool, parse_tiers
from jarvis_live.vault.index import NullVault, VaultContext, VaultIndex


def find_web_dist(cfg: Settings) -> Path | None:
    """The built web dashboard, if one is present."""
    candidates = [cfg.web_dist_dir] if cfg.web_dist_dir else [Path("web/dist")]
    if cfg.web_dist_dir is None and len(Path(__file__).resolve().parents) > 3:
        candidates.append(Path(__file__).resolve().parents[3] / "web" / "dist")
    for c in candidates:
        if c is not None and (c / "index.html").is_file():
            return c.resolve()
    return None


def _mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the SPA. Registered after every API route so it only sees unmatched GETs."""
    index = dist / "index.html"
    no_cache = {"Cache-Control": "no-cache"}

    @app.get("/{path:path}", include_in_schema=False)
    async def web(path: str) -> FileResponse:
        if path == "v1" or path.startswith("v1/") or path in {"healthz", "readyz"}:
            raise HTTPException(404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            # Vite fingerprints everything under assets/, so it can be cached forever.
            headers = (
                {"Cache-Control": "public, max-age=31536000, immutable"}
                if path.startswith("assets/")
                else no_cache
            )
            return FileResponse(candidate, headers=headers)
        if path.startswith("assets/") or Path(path).suffix:
            raise HTTPException(404)  # a missing file, not a client-side route
        return FileResponse(index, headers=no_cache)  # SPA fallback


def create_app(
    settings: Settings | None = None,
    *,
    stt: STTBackend | None = None,
    llm: LLM | None = None,
    notifier: Notifier | None = None,
) -> FastAPI:
    """``stt``, ``llm`` and ``notifier`` override what is built from settings (tests). The
    copilot and finalizer run only if an LLM is configured or injected."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or get_settings()
        if cfg.auto_migrate:
            await asyncio.to_thread(run_migrations, cfg.database_url)  # env.py owns a loop
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
        orchestrator: Orchestrator | None = None
        if (
            llm is not None or cfg.litellm_base_url
        ):  # copilot + finalizer need an LLM; without one, A2 behaviour
            if client is None:
                client = httpx.AsyncClient()
            vault: VaultContext = NullVault()
            if cfg.vault_clone_dir is not None:
                index = VaultIndex(
                    cfg.vault_clone_dir,
                    cfg.index_dir,
                    vault_name=cfg.vault_name,
                    exclude_globs=cfg.vault_exclude_globs,
                )
                vault = index
                tasks.append(
                    asyncio.create_task(index.run_refresh_loop(cfg.vault_refresh_interval_s))
                )
            orchestrator = Orchestrator(
                cfg,
                sm,
                bus,
                backend,
                llm
                or LiteLLMClient(
                    cfg.litellm_base_url, client, api_key_file=cfg.litellm_api_key_file
                ),
                vault,
                notifier or GotifyNotifier(cfg.gotify_url, cfg.gotify_token_file, client),
            )
        hub = IngestHub(
            cfg,
            sm,
            bus,
            backend,
            on_start=orchestrator.on_start if orchestrator else None,
            on_end=orchestrator.on_end if orchestrator else None,
        )
        if orchestrator is not None:
            orchestrator.attach(hub)
            await orchestrator.recover()
            tasks.append(asyncio.create_task(orchestrator.run_watchdog()))
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
            if orchestrator is not None:
                await orchestrator.shutdown()
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

    dist = find_web_dist(settings or get_settings())
    if dist is not None:
        _mount_web(app, dist)

    return app


app = create_app()
