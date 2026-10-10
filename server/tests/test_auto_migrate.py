from collections.abc import Callable

import httpx
import pytest

from jarvis_live import app as app_module
from tests.conftest import ServerHandle


@pytest.fixture
def migrations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    real = app_module.run_migrations

    def spy(url: str) -> None:
        calls.append(url)
        real(url)

    monkeypatch.setattr(app_module, "run_migrations", spy)
    return calls


def test_migrations_run_at_startup_when_enabled(
    make_server: Callable[..., ServerHandle], migrations: list[str], postgres_url: str
) -> None:
    handle = make_server(auto_migrate=True)
    assert migrations == [postgres_url]
    assert httpx.get(f"{handle.url}/healthz").json() == {"status": "ok"}


def test_migrations_are_not_run_by_default(
    make_server: Callable[..., ServerHandle], migrations: list[str]
) -> None:
    make_server()
    assert migrations == []
