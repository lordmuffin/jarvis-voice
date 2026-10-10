import pytest

from jarvis_live.config import Settings


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ],
)
def test_database_url_defaults_to_the_asyncpg_driver(given: str, expected: str) -> None:
    assert Settings(database_url=given).database_url == expected


def test_database_url_is_normalised_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LIVE_DATABASE_URL", "postgresql://u:p@h/d")
    assert Settings().database_url == "postgresql+asyncpg://u:p@h/d"


def test_auto_migrate_is_off_by_default() -> None:
    assert Settings().auto_migrate is False
