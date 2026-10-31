"""The action review API: the real app and a real Postgres."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from jarvis_live.actions.schema import Evidence, ProposalIn
from jarvis_live.actions.state import Context, ProposalState
from jarvis_live.db.models import OWNER_USER_ID, ActionProposal
from jarvis_live.users import create_user
from tests.conftest import ServerHandle
from tests.work_item_helpers import SM, device_client, owner_with_session, web_client

EV = [Evidence(start_ms=1000, text="add a task")]


@pytest.fixture
def aserver(make_server: Callable[..., ServerHandle]) -> ServerHandle:
    return make_server(actions_enabled=True)


async def seed(sm: SM, sid: uuid.UUID, user_id: uuid.UUID, *texts: str) -> list[dict[str, Any]]:
    from datetime import UTC, datetime

    ctx = Context(datetime.now(UTC), "UTC", lambda p: True, frozenset())
    props = ProposalState().apply(
        [ProposalIn(kind="task.create", text=t, evidence=EV) for t in texts], ctx
    )
    async with sm() as db:
        for i, p in enumerate(props, 1):
            db.add(
                ActionProposal(
                    session_id=sid,
                    user_id=user_id,
                    ref=p.ref,
                    position=i,
                    kind=p.kind,
                    payload=p.payload,
                    payload_sha256=p.payload_sha256,
                    dedupe_key=p.dedupe_key,
                    evidence=[e.model_dump() for e in p.evidence],
                    proposed_by="copilot:test",
                )
            )
        await db.commit()
    return [{"ref": p.ref, "sha": p.payload_sha256} for p in props]


async def listed(web: httpx.AsyncClient, sid: uuid.UUID) -> list[dict[str, Any]]:
    r = await web.get(f"/v1/sessions/{sid}/actions")
    assert r.status_code == 200, r.text
    return r.json()["items"]  # type: ignore[no-any-return]


async def test_list_shows_proposals_with_evidence_and_hash(aserver: ServerHandle, sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    [s1] = await seed(sm, sid, OWNER_USER_ID, "Call the vendor")
    async with await web_client(aserver, sm, OWNER_USER_ID) as web:
        page = (await web.get(f"/v1/sessions/{sid}/actions")).json()
        assert page["enabled"] is True
        [it] = page["items"]
        assert (it["ref"], it["kind"], it["state"]) == ("p1", "task.create", "proposed")
        assert it["payload_sha256"] == s1["sha"] and it["evidence"][0]["start_ms"] == 1000
        assert it["proposed_by"] == "copilot:test"


async def test_other_users_get_404(aserver: ServerHandle, sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    await seed(sm, sid, OWNER_USER_ID, "Call the vendor")
    other = await create_user(sm, f"other {uuid.uuid4().hex[:6]}")
    async with await web_client(aserver, sm, other.id) as web:
        assert await listed(web, sid) == []
        bogus = uuid.uuid4()
        assert (await web.post(f"/v1/actions/{bogus}/reject")).status_code == 404
        assert (await web.get(f"/v1/actions/{bogus}/events")).status_code == 404


async def test_approve_from_a_device_token_executes_and_audits(
    aserver: ServerHandle, sm: SM
) -> None:
    sid, token = await owner_with_session(sm, OWNER_USER_ID)
    [s1] = await seed(sm, sid, OWNER_USER_ID, "Call the vendor")
    async with await web_client(aserver, sm, OWNER_USER_ID) as web:
        pid = (await listed(web, sid))[0]["id"]
    async with device_client(aserver, token) as phone:  # macOS: a device may approve (D3)
        r = await phone.post(
            f"/v1/sessions/{sid}/actions/approve",
            json={"items": [{"id": pid, "payload_sha256": s1["sha"]}]},
        )
        assert r.status_code == 202, r.text
        assert r.json()["results"] == [{"id": pid, "outcome": "approved"}]
        for _ in range(50):  # no executor in this deployment: it fails, visibly
            it = (await phone.get(f"/v1/sessions/{sid}/actions")).json()["items"][0]
            if it["state"] != "approved" and it["state"] != "executing":
                break
            await asyncio.sleep(0.1)
        assert it["state"] == "failed" and "not available" in it["result"]["error"]
        events = (await phone.get(f"/v1/actions/{pid}/events")).json()["events"]
        assert [(e["event"], e["actor_kind"]) for e in events] == [
            ("approved", "device"),  # (seeded directly, so no "proposed" event)
            ("exec_started", "system"),
            ("exec_failed", "system"),
        ]


async def test_stale_hash_edit_and_reject(aserver: ServerHandle, sm: SM) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    [s1, s2] = await seed(sm, sid, OWNER_USER_ID, "one", "two")
    async with await web_client(aserver, sm, OWNER_USER_ID) as web:
        p1, p2 = [i["id"] for i in await listed(web, sid)]
        r = await web.patch(f"/v1/actions/{p1}", json={"changes": {"text": "one, edited"}})
        assert r.status_code == 200 and r.json()["payload"]["text"] == "one, edited"
        assert r.json()["payload_sha256"] != s1["sha"]
        bad = await web.patch(f"/v1/actions/{p1}", json={"changes": {"kind": "x"}})
        assert bad.status_code == 400
        r = await web.post(
            f"/v1/sessions/{sid}/actions/approve",
            json={"items": [{"id": p1, "payload_sha256": s1["sha"]}]},
        )
        assert r.json()["results"][0]["outcome"] == "stale"
        assert (await web.post(f"/v1/actions/{p2}/reject")).json()["state"] == "rejected"
        assert (await web.post(f"/v1/actions/{p1}/retry")).status_code == 409  # not failed


async def test_mutations_need_the_feature_on(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    off = make_server()
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    async with await web_client(off, sm, OWNER_USER_ID) as web:
        page = (await web.get(f"/v1/sessions/{sid}/actions")).json()
        assert page == {"enabled": False, "items": []}
        r = await web.post(f"/v1/sessions/{sid}/actions/approve", json={"items": []})
        assert r.status_code == 503
