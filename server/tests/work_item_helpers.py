"""Seeding work items and signing in for the work item API tests."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.auth import SESSION_COOKIE, create_login_session
from jarvis_live.db.models import WorkItem
from tests.conftest import ServerHandle
from tests.fakes import make_session, new_device

SM = async_sessionmaker  # type: ignore[type-arg]
REPOS = "lordmuffin/jarvis-voice  Jarvis Live\nlordmuffin/np-home-homelab  homelab"
SETTINGS: dict[str, Any] = {
    "work_items_enabled": True,
    "work_repos": REPOS,
    "public_base_url": "",
}


async def web_client(server: ServerHandle, sm: SM, user_id: uuid.UUID) -> httpx.AsyncClient:
    """The dashboard: a sign-in cookie, and the Origin header the CSRF guard wants."""
    cookie = await create_login_session(sm, user_id, 1)
    return httpx.AsyncClient(
        base_url=server.url,
        cookies={SESSION_COOKIE: cookie},
        headers={"Origin": server.url},
        timeout=30,
    )


def device_client(server: ServerHandle, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    )


async def seed_item(sm: SM, session_id: uuid.UUID, user_id: uuid.UUID, **kw: Any) -> uuid.UUID:
    async with sm() as db:
        n = kw.pop("n", 1)
        row = WorkItem(
            id=uuid.uuid4(),
            session_id=session_id,
            user_id=user_id,
            ref=kw.pop("ref", f"w{n}"),
            title=kw.pop("title", "Retry failed Gotify notifications"),
            repo=kw.pop("repo", "lordmuffin/jarvis-voice"),
            intent=kw.pop("intent", "Retry a failed push with backoff."),
            acceptance_criteria=kw.pop("acceptance_criteria", ["Retried with backoff"]),
            sources=kw.pop(
                "sources",
                [
                    {
                        "segment_id": str(uuid.uuid4()),
                        "start_ms": 21_000,
                        "end_ms": 30_000,
                        "text": "Retry it.",
                    }
                ],
            ),
            **kw,
        )
        db.add(row)
        await db.commit()
        return row.id


async def owner_with_session(sm: SM, user_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    """A device of ``user_id`` and a finished session of theirs: ``(session_id, token)``."""
    device_id, token = await new_device(sm, f"dev-{uuid.uuid4()}", user_id=user_id)
    return await make_session(sm, device_id, status="done"), token


Starter = Callable[..., ServerHandle]
