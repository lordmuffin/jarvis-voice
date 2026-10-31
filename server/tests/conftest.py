import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from docker.errors import DockerException
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from jarvis_live.app import create_app
from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID
from jarvis_live.db.session import make_engine, make_sessionmaker, run_migrations
from jarvis_live.diarize.client import Diarizer
from jarvis_live.dispatch.service import Kube, Tokens
from jarvis_live.llm.client import LLM
from jarvis_live.notify.gotify import Notifier
from jarvis_live.stt.backend import STTBackend, Transcription
from jarvis_live.workitems.dispatch import Dispatcher
from tests.fake_github import TOKEN as GITHUB_TOKEN
from tests.fake_github import FakeGitHub
from tests.fake_oidc import CLIENT_ID, CLIENT_SECRET, FakeIssuer
from tests.fakes import new_device


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", help="run tests that hit a real Whisper")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Anything that (transitively) needs the Postgres fixture is an integration test, so the
    # fast suite (`pytest -m "not integration"`) runs without Docker.
    for item in items:
        if "postgres_url" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.integration)
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


def _serve(
    app: Any, running: list[tuple[uvicorn.Server, threading.Thread]], port: int | None = None
) -> int:
    port = port or _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    running.append((server, thread))
    return port


def _stop(running: list[tuple[uvicorn.Server, threading.Thread]]) -> None:
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def make_server(postgres_url: str, tmp_path: Path) -> Iterator[Callable[..., ServerHandle]]:
    """Run the real app under uvicorn in a thread (own event loop), like production."""
    running: list[tuple[uvicorn.Server, threading.Thread]] = []

    def start(
        stt: STTBackend | None = None,
        llm: LLM | None = None,
        notifier: Notifier | None = None,
        dispatcher: Dispatcher | None = None,
        kube: Kube | None = None,
        app_tokens: Tokens | None = None,
        diarizer: Diarizer | None = None,
        **overrides: object,
    ) -> ServerHandle:
        settings = Settings(database_url=postgres_url, data_dir=tmp_path / "data", **overrides)  # type: ignore[arg-type]
        stt = stt or FakeSTT()
        app = create_app(
            settings,
            stt=stt,
            llm=llm,
            notifier=notifier,
            dispatcher=dispatcher,
            kube=kube,
            app_tokens=app_tokens,
            diarizer=diarizer,
        )
        port = _serve(app, running)
        return ServerHandle(f"http://127.0.0.1:{port}", settings, stt)

    yield start
    _stop(running)


@pytest.fixture
def issuer() -> Iterator[FakeIssuer]:
    """A fake Pocket-ID on its own port."""
    running: list[tuple[uvicorn.Server, threading.Thread]] = []
    port = _free_port()
    fake = FakeIssuer(f"http://127.0.0.1:{port}")
    _serve(fake.app(), running, port)
    yield fake
    _stop(running)


@pytest.fixture
def oidc_settings(issuer: FakeIssuer, tmp_path: Path) -> dict[str, Any]:
    secret = tmp_path / "oidc-secret"
    secret.write_text(CLIENT_SECRET + "\n")
    return {
        "oidc_issuer": issuer.issuer,
        "oidc_client_id": CLIENT_ID,
        "oidc_client_secret_file": secret,
        "oidc_admin_group": "jarvis-admins",
    }


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    """A fake GitHub API (holding the vault repo) on its own port."""
    running: list[tuple[uvicorn.Server, threading.Thread]] = []
    port = _free_port()
    fake = FakeGitHub(f"http://127.0.0.1:{port}")
    _serve(fake.app(), running, port)
    yield fake
    _stop(running)


@pytest.fixture
def publish_settings(github: FakeGitHub, tmp_path: Path) -> dict[str, Any]:
    token = tmp_path / "github-token"
    token.write_text(GITHUB_TOKEN + "\n")
    return {
        "publish_repo": github.repo,
        "github_api": github.url,
        "github_token_file": token,
        "publish_reconcile_interval_s": 0,
        "public_base_url": "https://live.example",
    }


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
    """A device of the deployment owner (whose sessions notify and see the vault)."""
    _, tok = await new_device(sm, "test-device", user_id=OWNER_USER_ID)
    return tok
