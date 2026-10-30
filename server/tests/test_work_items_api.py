"""The work item review API: the real app and a real Postgres."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select

from jarvis_live.db.models import OWNER_USER_ID, WorkItemEvent
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


@pytest.fixture
def wserver(make_server: Callable[..., ServerHandle]) -> ServerHandle:
    return make_server(**SETTINGS)


async def audit(sm: SM, item_id: uuid.UUID) -> list[tuple[str, str]]:
    async with sm() as db:
        rows = await db.execute(
            select(WorkItemEvent.event, WorkItemEvent.actor_kind)
            .where(WorkItemEvent.work_item_id == item_id)
            .order_by(WorkItemEvent.id)
        )
        return [(e, k) for e, k in rows.all()]


def item_ids(page: dict[str, Any]) -> list[str]:
    return [i["id"] for i in page["items"]]


async def test_list_shows_items_repos_and_what_blocks_approval(
    wserver: ServerHandle, sm: SM
) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    await seed_item(sm, sid, OWNER_USER_ID)
    await seed_item(
        sm, sid, OWNER_USER_ID, n=2, title="SRT export CLI", repo=None, acceptance_criteria=[]
    )
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        page = (await web.get(f"/v1/sessions/{sid}/work-items")).json()
        assert page["enabled"] is True and page["dispatch_enabled"] is False
        assert [r["name"] for r in page["repos"]] == [
            "lordmuffin/jarvis-voice",
            "lordmuffin/np-home-homelab",
        ]
        w1, w2 = page["items"]
        assert w1["problems"] == []
        assert w2["problems"] == ["choose a repository", "add at least one acceptance criterion"]
        assert w1["branch"].startswith(f"claude/work-{str(sid)[:6]}-w1-retry-failed")
        assert (await web.get("/v1/me")).json()["work_items"] is True
        mine = (await web.get("/v1/work-items")).json()["items"]
        assert {i["id"] for i in page["items"]} <= {i["id"] for i in mine}
        assert mine[0]["session_title"] is None and mine[0]["session_started_at"]


async def test_other_users_get_404(wserver: ServerHandle, sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    other = await create_user(sm, f"other {uuid.uuid4().hex[:6]}")
    async with await web_client(wserver, sm, other.id) as web:
        assert (await web.get("/v1/me")).json()["work_items"] is False  # not owner or admin
        assert (await web.get(f"/v1/sessions/{sid}/work-items")).status_code == 404
        assert (await web.get(f"/v1/work-items/{item}")).status_code == 404
        assert (await web.post(f"/v1/work-items/{item}/approve")).status_code == 404
        assert (await web.patch(f"/v1/work-items/{item}", json={"title": "x"})).status_code == 404


async def test_edit_locks_fields_and_validates(wserver: ServerHandle, sm: SM) -> None:
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with device_client(wserver, token) as phone:  # a device token may edit
        r = await phone.patch(
            f"/v1/work-items/{item}",
            json={"title": "  Retry Gotify  ", "acceptance_criteria": ["a", "a", " b "]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["title"] == "Retry Gotify"
        assert body["acceptance_criteria"] == ["a", "b"]
        assert body["locked_fields"] == ["acceptance_criteria", "title"]
        assert (
            await phone.patch(f"/v1/work-items/{item}", json={"repo": "evil/repo"})
        ).status_code == 422
        assert (await phone.patch(f"/v1/work-items/{item}", json={"title": " "})).status_code == 422
    assert await audit(sm, item) == [("edited", "device")]


async def test_approve_freezes_the_prompt_and_an_edit_unapproves(
    wserver: ServerHandle, sm: SM
) -> None:
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with device_client(wserver, token) as phone:
        r = await phone.post(f"/v1/work-items/{item}/approve")
        assert r.status_code == 403  # approval needs the dashboard sign-in
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        preview = (await web.get(f"/v1/work-items/{item}/prompt")).json()
        assert preview["frozen"] is False and preview["sha256"] is None
        r = await web.post(f"/v1/work-items/{item}/approve")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "approved"
        frozen = (await web.get(f"/v1/work-items/{item}/prompt")).json()
        assert frozen["frozen"] is True and frozen["markdown"] == preview["markdown"]
        assert frozen["sha256"] == r.json()["prompt_sha256"]
        assert (await web.post(f"/v1/work-items/{item}/copied")).status_code == 204
        assert (await web.post(f"/v1/work-items/{item}/approve")).status_code == 409  # twice

        r = await web.patch(f"/v1/work-items/{item}", json={"intent": "Something else."})
        assert r.json()["state"] == "draft" and r.json()["prompt_sha256"] is None
        events = (await web.get(f"/v1/work-items/{item}/events")).json()
    assert [e["event"] for e in events] == ["approved", "prompt_copied", "unapproved", "edited"]
    assert events[0]["actor_kind"] == "web"
    assert events[0]["payload"]["prompt"] == preview["markdown"]


async def test_approval_needs_a_repo_and_criteria(wserver: ServerHandle, sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID, repo=None, acceptance_criteria=[])
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        r = await web.post(f"/v1/work-items/{item}/approve")
        assert r.status_code == 409
        assert "choose a repository" in r.json()["detail"]
        await web.patch(
            f"/v1/work-items/{item}",
            json={"repo": "lordmuffin/np-home-homelab", "acceptance_criteria": ["It alerts"]},
        )
        assert (await web.post(f"/v1/work-items/{item}/approve")).status_code == 200


async def test_discard_restore_and_unapprove(wserver: ServerHandle, sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        await web.post(f"/v1/work-items/{item}/approve")
        r = await web.post(f"/v1/work-items/{item}/unapprove")
        assert r.json()["state"] == "draft"
        assert (await web.post(f"/v1/work-items/{item}/discard")).json()["state"] == "discarded"
        assert (await web.patch(f"/v1/work-items/{item}", json={"title": "x"})).status_code == 409
        listed = (await web.get("/v1/work-items")).json()["items"]
        assert str(item) not in {i["id"] for i in listed}  # drafts and approved by default
        gone = (await web.get("/v1/work-items?state=discarded")).json()["items"]
        assert str(item) in {i["id"] for i in gone}
        assert (await web.post(f"/v1/work-items/{item}/restore")).json()["state"] == "draft"


async def test_merge_folds_drafts_into_one(wserver: ServerHandle, sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    s1 = {"segment_id": "a", "start_ms": 1000, "end_ms": 2000, "text": "one"}
    s2 = {"segment_id": "b", "start_ms": 500, "end_ms": 900, "text": "two"}
    into = await seed_item(sm, sid, OWNER_USER_ID, sources=[s1], acceptance_criteria=["A"])
    other = await seed_item(
        sm,
        sid,
        OWNER_USER_ID,
        n=2,
        title="Log when Gotify gives up",
        intent="Log the final failure.",
        sources=[s2, s1],
        acceptance_criteria=["A", "B"],
    )
    elsewhere_sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    elsewhere = await seed_item(sm, elsewhere_sid, OWNER_USER_ID)
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        r = await web.post(
            "/v1/work-items/merge", json={"into": str(into), "sources": [str(elsewhere)]}
        )
        assert r.status_code == 409  # another session
        r = await web.post(
            "/v1/work-items/merge", json={"into": str(into), "sources": [str(other)]}
        )
        assert r.status_code == 200, r.text
        merged = r.json()
        assert [s["segment_id"] for s in merged["sources"]] == ["b", "a"]
        assert merged["acceptance_criteria"] == ["A", "B"]
        assert merged["intent"] == "Retry a failed push with backoff.\n\nLog the final failure."
        assert set(merged["locked_fields"]) >= {"title", "intent", "acceptance_criteria"}
        folded = (await web.get(f"/v1/work-items/{other}")).json()
        assert folded["state"] == "merged" and folded["merged_into"] == str(into)


async def test_work_items_off(make_server: Callable[..., ServerHandle], sm: SM) -> None:
    server = make_server()
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(server, sm, OWNER_USER_ID) as web:
        assert (await web.get(f"/v1/sessions/{sid}/work-items")).json()["enabled"] is False
        assert (await web.post(f"/v1/work-items/{item}/approve")).status_code == 503


async def test_mark_done_closes_an_approved_item_and_reopen_brings_it_back_approved(
    wserver: ServerHandle, sm: SM
) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        approved = (await web.post(f"/v1/work-items/{item}/approve")).json()

        done = (await web.post(f"/v1/work-items/{item}/done")).json()
        assert done["state"] == "done" and done["done_at"] and done["merged_pr_url"] is None
        assert str(item) not in item_ids((await web.get("/v1/work-items")).json())
        assert str(item) in item_ids((await web.get("/v1/work-items?state=done")).json())
        assert (await web.patch(f"/v1/work-items/{item}", json={"title": "x"})).status_code == 409
        assert (await web.post(f"/v1/work-items/{item}/discard")).status_code == 409

        back = (await web.post(f"/v1/work-items/{item}/reopen")).json()
        assert back["state"] == "approved" and back["done_at"] is None
        assert back["prompt_sha256"] == approved["prompt_sha256"]
    assert [e for e, _ in await audit(sm, item)][-2:] == ["done", "reopened"]


async def test_a_draft_can_be_marked_done_and_reopens_as_a_draft(
    wserver: ServerHandle, sm: SM
) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        assert (await web.post(f"/v1/work-items/{item}/done")).json()["state"] == "done"
        assert (await web.post(f"/v1/work-items/{item}/reopen")).json()["state"] == "draft"


async def test_done_and_reopen_refuse_the_wrong_state_and_other_owners(
    wserver: ServerHandle, sm: SM
) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    stranger = await create_user(sm, f"s-{uuid.uuid4()}@example.com")
    async with await web_client(wserver, sm, stranger.id) as other:
        assert (await other.post(f"/v1/work-items/{item}/done")).status_code == 404
    async with await web_client(wserver, sm, OWNER_USER_ID) as web:
        assert (await web.post(f"/v1/work-items/{item}/reopen")).status_code == 409
        await web.post(f"/v1/work-items/{item}/discard")
        assert (await web.post(f"/v1/work-items/{item}/done")).status_code == 409
