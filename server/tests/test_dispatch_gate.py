"""Integration: nothing is dispatched without the owner's explicit approval.

The real app and Postgres, with a spy dispatcher injected. Every refused case must answer 409,
write a ``dispatch_refused`` audit row, and never reach the dispatcher."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select, update

from jarvis_live.db.models import OWNER_USER_ID, WorkItem, WorkItemEvent
from jarvis_live.users import create_user
from tests.conftest import ServerHandle
from tests.work_item_helpers import (
    SETTINGS,
    SM,
    device_client,
    owner_with_session,
    seed_item,
    web_client,
)


class SpyDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def dispatch(self, item_id: uuid.UUID, **kw: Any) -> None:
        self.calls.append({"item_id": item_id, **kw})


@pytest.fixture
def spy() -> SpyDispatcher:
    return SpyDispatcher()


async def refused(sm: SM, item_id: uuid.UUID) -> list[str]:
    async with sm() as db:
        rows = await db.execute(
            select(WorkItemEvent.payload)
            .where(WorkItemEvent.work_item_id == item_id, WorkItemEvent.event == "dispatch_refused")
            .order_by(WorkItemEvent.id)
        )
        return [p["reason"] for p in rows.scalars().all()]


@pytest.fixture
def server_with_spy(make_server: Callable[..., ServerHandle], spy: SpyDispatcher) -> ServerHandle:
    return make_server(dispatcher=spy, **SETTINGS)


async def test_dispatch_is_blocked_without_approval(
    server_with_spy: ServerHandle, sm: SM, spy: SpyDispatcher
) -> None:
    server = server_with_spy
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    draft = await seed_item(sm, sid, OWNER_USER_ID, n=1)
    discarded = await seed_item(sm, sid, OWNER_USER_ID, n=2, title="Discarded", state="discarded")
    edited = await seed_item(sm, sid, OWNER_USER_ID, n=3, title="Edited after approval")
    delisted = await seed_item(sm, sid, OWNER_USER_ID, n=4, title="Repo removed from the list")
    phone_only = await seed_item(sm, sid, OWNER_USER_ID, n=5, title="Approved, dispatched by phone")

    async with await web_client(server, sm, OWNER_USER_ID) as web:
        for item in (edited, delisted, phone_only):
            assert (await web.post(f"/v1/work-items/{item}/approve")).status_code == 200
        # Tamper with the frozen prompt directly in the DB: the sha no longer matches.
        async with sm() as db:
            await db.execute(
                update(WorkItem).where(WorkItem.id == edited).values(prompt_md="rm -rf /")
            )
            await db.execute(
                update(WorkItem).where(WorkItem.id == delisted).values(repo="someone/else")
            )
            await db.commit()

        cases = {
            draft: "not approved",
            discarded: "not approved",
            edited: "prompt changed since it was approved",
            delisted: "not on the allowlist",
        }
        for item, reason in cases.items():
            r = await web.post(f"/v1/work-items/{item}/dispatch")
            assert r.status_code == 409, (item, r.text)
            assert reason in r.json()["detail"]
            assert any(reason in x for x in await refused(sm, item))

    async with device_client(server, token) as phone:
        r = await phone.post(f"/v1/work-items/{phone_only}/dispatch")
        assert r.status_code == 409 and "device token" in r.json()["detail"]
        assert await refused(sm, phone_only)

    other = await create_user(sm, f"other {uuid.uuid4().hex[:6]}")
    async with await web_client(server, sm, other.id) as web:
        assert (await web.post(f"/v1/work-items/{phone_only}/dispatch")).status_code == 404

    assert spy.calls == []


async def test_an_approved_item_reaches_the_dispatcher_with_its_frozen_prompt(
    server_with_spy: ServerHandle, sm: SM, spy: SpyDispatcher
) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(server_with_spy, sm, OWNER_USER_ID) as web:
        approved = (await web.post(f"/v1/work-items/{item}/approve")).json()
        prompt = (await web.get(f"/v1/work-items/{item}/prompt")).json()
        r = await web.post(f"/v1/work-items/{item}/dispatch")
        assert r.status_code == 202, r.text
    assert spy.calls == [
        {
            "item_id": item,
            "repo": "lordmuffin/jarvis-voice",
            "prompt": prompt["markdown"],
            "prompt_sha256": approved["prompt_sha256"],
            "actor_user_id": OWNER_USER_ID,
        }
    ]
    async with sm() as db:
        logged = (
            await db.execute(
                select(WorkItemEvent.payload).where(
                    WorkItemEvent.work_item_id == item, WorkItemEvent.event == "dispatch_requested"
                )
            )
        ).scalar_one()
    assert logged["prompt"] == prompt["markdown"] and logged["repo"] == "lordmuffin/jarvis-voice"


async def test_without_a_dispatcher_even_approved_items_get_503(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    server = make_server(**SETTINGS)
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(server, sm, OWNER_USER_ID) as web:
        await web.post(f"/v1/work-items/{item}/approve")
        r = await web.post(f"/v1/work-items/{item}/dispatch")
        assert r.status_code == 503
    assert await refused(sm, item) == ["Dispatch is not configured on this server."]


async def test_a_repo_off_the_dispatch_allowlist_is_refused(
    make_server: Callable[..., ServerHandle], sm: SM, spy: SpyDispatcher
) -> None:
    """Lab drafts work items for the real repos but may only dispatch to its sandbox."""
    server = make_server(
        dispatcher=spy, dispatch_repos="lordmuffin/jarvis-dispatch-sandbox", **SETTINGS
    )
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)  # lordmuffin/jarvis-voice
    async with await web_client(server, sm, OWNER_USER_ID) as web:
        assert (await web.post(f"/v1/work-items/{item}/approve")).status_code == 200
        r = await web.post(f"/v1/work-items/{item}/dispatch")
        assert r.status_code == 409
        assert "dispatch allowlist" in r.json()["detail"]
    assert spy.calls == []
