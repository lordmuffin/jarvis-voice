from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

_ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"
_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(engine, expire_on_commit=False)


def alembic_config(url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def run_migrations(url: str) -> None:
    """Upgrade to head. Synchronous: env.py runs its own event loop, so call it from a thread
    (``asyncio.to_thread``) when inside a running loop."""
    command.upgrade(alembic_config(url), "head")
