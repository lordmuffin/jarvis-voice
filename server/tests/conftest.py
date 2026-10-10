import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn
from docker.errors import DockerException
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from jarvis_live.app import create_app
from jarvis_live.auth import create_device
from jarvis_live.config import Settings
from jarvis_live.db.session import make_engine, make_sessionmaker, run_migrations
from jarvis_live.llm.client import LLM
from jarvis_live.notify.gotify import Notifier
from jarvis_live.stt.backend import STTBackend, Transcription


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="run tests that hit a real Whisper")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


class FakeSTT:
    """Returns ``seg-{n}`` for the n-th call."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self.tier = "fake"

    async def transcribe(self, pcm: bytes, prompt: str) -> Transcription:
        self.calls += 1
        self.prompts.append(prompt)
        return Transcription(text=f"seg-{self.calls}", tier=self.tier)

    def current_tier(self) -> str | None:
        return self.tier


def _asyncpg_url(url: str) -> str:
    """Accept plain postgres:// and postgresql:// URLs; the app only ships the asyncpg driver."""
    for plain in ("postgresql://", "postgres://"):
        if url.startswith(plain):
            return "postgresql+asyncpg://" + url[len(plain) :]
    return url


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    if url := os.environ.get("JARVIS_LIVE_TEST_DATABASE_URL"):  # no Docker: use this server
        url = _asyncpg_url(url)
        run_migrations(url)
        yield url
        return
    try:  # the Docker client connects in the constructor
        container = PostgresContainer(
            os.environ.get("JARVIS_LIVE_TEST_POSTGRES_IMAGE", "postgres:16-alpine"),
            driver="asyncpg",
        )
        container.start()
    except DockerException:
        pytest.fail(
            "Docker is not reachable. Start Docker or set JARVIS_LIVE_TEST_DATABASE_URL "
            "to a Postgres URL.",
            pytrace=False,
        )
    try:
        url = container.get_connection_url()
        run_migrations(url)
        yield url
    finally:
        container.stop()


@dataclass
class ServerHandle:
    url: str
    settings: Settings
    stt: STTBackend


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def make_server(postgres_url: str, tmp_path: Path) -> Iterator[Callable[..., ServerHandle]]:
    """Run the real app under uvicorn in a thread (own event loop), like production."""
    running: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(
        stt: STTBackend | None = None,
        llm: LLM | None = None,
        notifier: Notifier | None = None,
        **overrides: object,
    ) -> ServerHandle:
        settings = Settings(database_url=postgres_url, data_dir=tmp_path / "data", **overrides)  # type: ignore[arg-type]
        stt = stt or FakeSTT()
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(settings, stt=stt, llm=llm, notifier=notifier),
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="on",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.02)
        running.append((server, thread))
        return ServerHandle(f"http://127.0.0.1:{port}", settings, stt)

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def server(make_server: Callable[..., ServerHandle]) -> ServerHandle:
    return make_server()


@pytest.fixture
async def engine(postgres_url: str) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(postgres_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def sm(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return make_sessionmaker(engine)


@pytest.fixture
async def token(sm: async_sessionmaker) -> str:  # type: ignore[type-arg]
    _, tok = await create_device(sm, "test-device")
    return tok
