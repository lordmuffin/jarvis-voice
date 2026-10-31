import asyncio
import importlib.metadata
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from jarvis_live.api import (
    account,
    auth,
    homelab,
    publishing,
    sessions,
    speakers,
    stream,
    work_items,
)
from jarvis_live.apps import load_family_apps
from jarvis_live.auth import TicketStore
from jarvis_live.bus import Bus
from jarvis_live.config import Settings, get_settings
from jarvis_live.db.session import make_engine, make_sessionmaker, run_migrations
from jarvis_live.diarize.client import Diarizer, HttpDiarizer
from jarvis_live.dispatch.githubapp import AppTokens
from jarvis_live.dispatch.kube import KubeClient
from jarvis_live.dispatch.service import DispatchService, Kube, Tokens, dispatch_config_problems
from jarvis_live.homelab.client import DpiClient
from jarvis_live.ingest.hub import IngestHub
from jarvis_live.ingest.upload import Uploads
from jarvis_live.llm.client import LLM, LiteLLMClient
from jarvis_live.notify.gotify import GotifyNotifier, Notifier
from jarvis_live.oidc import OIDCClient
from jarvis_live.orchestrator import Orchestrator
from jarvis_live.publish.github import GitHubClient
from jarvis_live.publish.service import PublishService
from jarvis_live.retention import run_retention_loop
from jarvis_live.stt.backend import STTBackend, WhisperBackend
from jarvis_live.stt.tiers import TierPool, parse_tiers
from jarvis_live.vault.index import NullVault, VaultContext, VaultIndex
from jarvis_live.workitems.dispatch import Dispatcher
from jarvis_live.workitems.prwatch import PrWatch
from jarvis_live.workitems.service import WorkItemService

log = logging.getLogger(__name__)


def _version() -> str:
    try:
        return importlib.metadata.version("jarvis-live")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


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
        if (
            path in {"v1", "auth"}
            or path.startswith(("v1/", "auth/"))
            or path in {"healthz", "readyz"}
        ):
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
    github: GitHubClient | None = None,
    dispatcher: Dispatcher | None = None,
    kube: Kube | None = None,
    app_tokens: Tokens | None = None,
    dpi: DpiClient | None = None,
    diarizer: Diarizer | None = None,
) -> FastAPI:
    """``stt``, ``llm``, ``notifier``, ``github``, ``dispatcher`` and ``diarizer`` override what
    is built from settings (tests). The copilot and finalizer run only if an LLM is configured or
    injected; notes are published only if ``publish_repo`` and ``github_token_file`` are set.
    Work items are found only with ``work_items_enabled`` and an LLM. With
    ``dispatch_enabled``, approved items run as Kubernetes Jobs (``kube`` and ``app_tokens``
    stand in for the cluster and the GitHub App in tests). ``dpi`` overrides the Pi Durable
    client built from ``dpi_base_url``/``dpi_token_file`` (tests); unset and unconfigured,
    ``POST /v1/homelab/ask`` always answers with the fallback phrase."""

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
        gh = github
        if gh is None and cfg.publish_enabled:
            if client is None:
                client = httpx.AsyncClient()
            assert cfg.github_token_file is not None
            gh = GitHubClient(
                client, api=cfg.github_api, repo=cfg.publish_repo, token_file=cfg.github_token_file
            )
        publisher = PublishService(cfg, sm, gh)
        dispatch: DispatchService | None = None
        runner: Dispatcher | None = dispatcher
        if cfg.dispatch_enabled and runner is None:
            if problems := dispatch_config_problems(cfg, own_tokens=app_tokens is None):
                # Off, loudly, not a crash loop: e.g. lab enabled before its secrets exist.
                log.error("dispatch is enabled but stays off: %s", "; ".join(problems))
            else:
                if client is None:
                    client = httpx.AsyncClient()
                dispatch = DispatchService(
                    cfg,
                    sm,
                    kube or KubeClient.in_cluster(cfg.dispatch_namespace),
                    app_tokens
                    or AppTokens(
                        client,
                        api=cfg.github_api,
                        app_id=cfg.github_app_id,
                        installation_id=cfg.github_app_installation_id,
                        key_file=cfg.github_app_key_file or Path(),
                    ),
                    notifier
                    or GotifyNotifier(
                        cfg.gotify_url,
                        cfg.gotify_token_file,
                        client,
                        title_prefix=cfg.notify_title_prefix,
                    ),
                )
                runner = dispatch
                tasks.append(asyncio.create_task(dispatch.run_loop()))
        work = WorkItemService(cfg, sm, runner)
        watching = cfg.forgejo_url and cfg.forgejo_token_file and cfg.work_forgejo_repo_map
        if work.enabled and watching and cfg.work_pr_poll_s > 0:
            if client is None:
                client = httpx.AsyncClient()
            tasks.append(
                asyncio.create_task(PrWatch(cfg, sm, work, client).run_loop(cfg.work_pr_poll_s))
            )
        if publisher.enabled and cfg.publish_reconcile_interval_s > 0:
            tasks.append(
                asyncio.create_task(publisher.run_reconcile_loop(cfg.publish_reconcile_interval_s))
            )
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
                notifier
                or GotifyNotifier(
                    cfg.gotify_url,
                    cfg.gotify_token_file,
                    client,
                    title_prefix=cfg.notify_title_prefix,
                ),
                on_final=publisher.on_note_ready,
                diarizer=diarizer
                or (
                    HttpDiarizer(cfg.diarize_url, client, timeout_s=None)
                    if cfg.diarize_url
                    else None
                ),
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
        oidc: OIDCClient | None = None
        if cfg.oidc_issuer:
            if client is None:
                client = httpx.AsyncClient()
            oidc = OIDCClient(
                cfg.oidc_issuer, cfg.oidc_client_id, cfg.oidc_client_secret_file, client
            )
        dpi_client = dpi
        if dpi_client is None and cfg.dpi_base_url:
            if client is None:
                client = httpx.AsyncClient()
            dpi_client = DpiClient(
                cfg.dpi_base_url,
                cfg.dpi_token_file,
                client,
                session=cfg.dpi_session,
                wait_ms=cfg.dpi_wait_ms,
            )
        app.state.dpi = dpi_client
        app.state.oidc = oidc
        app.state.settings = cfg
        app.state.sessionmaker = sm
        app.state.bus = bus
        app.state.tickets = TicketStore(ttl_s=60)
        app.state.hub = hub
        uploads = app.state.uploads = Uploads(hub)
        app.state.orchestrator = orchestrator
        app.state.publishing = publisher
        app.state.work_items = work
        app.state.dispatch = dispatch
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
            await publisher.shutdown()
            await uploads.shutdown()
            if orchestrator is not None:
                await orchestrator.shutdown()
            await hub.shutdown()
            if client is not None:
                await client.aclose()
            await engine.dispose()

    app = FastAPI(title="Jarvis Live", lifespan=lifespan)
    app.include_router(auth.router)
    app.include_router(account.router)
    app.include_router(sessions.router)
    app.include_router(speakers.router)
    app.include_router(publishing.router)
    app.include_router(work_items.router)
    app.include_router(stream.router)
    app.include_router(homelab.router)

    cfg = settings or get_settings()
    environment = cfg.environment
    version = _version()
    commit = cfg.git_sha.strip()[:7]
    app.state.family_apps = load_family_apps(cfg)  # cached for the life of the process

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up. Also says which release channel, version and commit
        this is, so lab and live can be told apart (and checked after a deploy)."""
        return {"status": "ok", "environment": environment, "version": version, "commit": commit}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        """Readiness: always ok until dependencies (DB, STT) are wired in."""
        return {"status": "ok"}

    dist = find_web_dist(cfg)
    if dist is not None:
        _mount_web(app, dist)

    return app


app = create_app()
