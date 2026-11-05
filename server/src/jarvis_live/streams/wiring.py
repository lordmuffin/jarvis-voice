"""Builds the stream ingest and its auth hook from settings (``app.py`` runs them)."""

import asyncio
import contextlib
import secrets
import socket
import uuid
from collections.abc import Iterator

import httpx
import uvicorn

from jarvis_live.auth import TicketStore
from jarvis_live.config import Settings
from jarvis_live.streams import keys
from jarvis_live.streams.auth import ReaderCredential, StreamAuth, auth_app
from jarvis_live.streams.ingest import StreamIngest
from jarvis_live.streams.mediamtx import MediaMtx

READER_USER = "jarvis-ingest"


class _HookServer(uvicorn.Server):
    """A second uvicorn server inside the app's process: it must leave signal handling to the
    main one, or SIGTERM would stop only the hook. ``listening`` is set once MediaMTX can
    connect: the ingest polls MediaMTX only after that (each poll is a call to this hook)."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.listening = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started:
            self.listening.set()

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def build(
    cfg: Settings, sm: keys.SM, tickets: TicketStore, client: httpx.AsyncClient
) -> tuple[StreamIngest, _HookServer]:
    # Generated per process: MediaMTX gets it from no config, only from this server's reads.
    reader = ReaderCredential(READER_USER, secrets.token_urlsafe(24))
    paths = MediaMtx(cfg.streams_mediamtx_api, reader, client, rtsp=cfg.streams_mediamtx_rtsp)

    async def ticket_for(session_id: uuid.UUID, user_id: uuid.UUID) -> str:
        return tickets.issue(session_id, user_id, "producer")

    ingest = StreamIngest(
        sm,
        paths,
        server_url=cfg.streams_self_url,
        ticket_for=ticket_for,
        state_dir=cfg.data_dir / "streams",
        stop_grace_s=cfg.streams_stop_grace_s,
        max_streams=cfg.streams_max,
        ffmpeg=cfg.ffmpeg_path,
    )
    hook = _HookServer(
        uvicorn.Config(
            auth_app(StreamAuth(sm, reader, ingest.has_room)),
            host="0.0.0.0",  # noqa: S104  MediaMTX is another pod; a NetworkPolicy fences it
            port=cfg.streams_auth_port,
            log_config=None,
            lifespan="off",
        )
    )
    return ingest, hook
