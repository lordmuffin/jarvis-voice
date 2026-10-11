"""Publishing notes to the vault repo: the real app against a fake GitHub and a real Postgres."""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from jarvis_live.copilot.schema import FinalNote
from jarvis_live.db.models import OWNER_USER_ID, CopilotItem, Device, User
from jarvis_live.publish import service as publish_service
from jarvis_live.users import create_user
from tests.conftest import ServerHandle
from tests.fake_github import FakeGitHub
from tests.fakes import FakeLLM, add_segments, make_session, new_device

SM = async_sessionmaker  # type: ignore[type-arg]
SUBDIR = "00 Inbox/Live Sessions"


def note_md(sid: uuid.UUID, body: str = "Talked about things.") -> str:
    return f"---\nsession_id: {sid}\ntype: live-session\n---\n\n# Note\n\n{body}\n"


async def finished(
    sm: SM, outbox: Path, device_id: uuid.UUID, name: str | None = None, title: str = "Sync"
) -> uuid.UUID:
    """A ``done`` session with a note in the outbox, as the finalizer leaves it."""
    sid = await make_session(sm, device_id, status="done")
    async with sm() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        user = await db.get(User, device.user_id)
        assert user is not None
        rel = name or f"2026-10-09 0930 - {sid.hex[:6]}.md"
        if user.id != OWNER_USER_ID and user.notes_subdir:
            rel = f"{user.notes_subdir}/{rel}"
        db.add(
            CopilotItem(session_id=sid, kind="final_note", payload={"path": rel, "title": title})
        )
        await db.commit()
    (outbox / rel).parent.mkdir(parents=True, exist_ok=True)
    (outbox / rel).write_text(note_md(sid))
    return sid


def client(server: ServerHandle, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=server.url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    )


@pytest.fixture
def pub_server(
    make_server: Callable[..., ServerHandle], publish_settings: dict[str, Any]
) -> Callable[..., ServerHandle]:
    return lambda **kw: make_server(**{**publish_settings, **kw})


def outbox_of(server: ServerHandle) -> Path:
    return server.settings.data_dir / "outbox"


async def test_publish_edit_merge_and_republish(
    pub_server: Callable[..., ServerHandle], github: FakeGitHub, sm: SM, token: str
) -> None:
    server = pub_server()
    outbox = outbox_of(server)
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, outbox, device_id)
    async with client(server, token) as c:
        d = (await c.get(f"/v1/sessions/{sid}")).json()
        assert d["can_publish"] is True and d["publication"] is None

        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 200, r.text
        pub = r.json()["publication"]
        assert pub["state"] == "open" and pub["changed"] is False
        note_path = r.json()["final_note"]["path"]
        assert pub["repo_path"] == f"{SUBDIR}/{note_path}"
        assert pub["branch"] == f"jarvis-live/2026-10-09-{sid.hex[:8]}"
        pr = github.pulls[pub["pr_number"]]
        assert pr.base == "main" and f"https://live.example/sessions/{sid}" in pr.body
        assert github.files(pub["branch"])[pub["repo_path"]] == note_md(sid)
        assert pub["repo_path"] not in github.files("main")  # proposed, not pushed to main

        # A second publish while the PR is open is refused.
        assert (await c.post(f"/v1/sessions/{sid}/publication")).status_code == 409

        # An edit lands in the outbox and on the open PR's branch.
        edited = note_md(sid, "Edited by hand.")
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": edited})
        assert r.status_code == 200, r.text
        assert r.json()["final_note"]["markdown"] == edited
        assert r.json()["publication"]["changed"] is False
        assert (outbox / note_path).read_text() == edited
        assert github.files(pub["branch"])[pub["repo_path"]] == edited

        r = await c.post(f"/v1/sessions/{sid}/publication/merge")
        assert r.status_code == 200, r.text
        assert r.json()["publication"]["state"] == "merged"
        assert github.files("main")[pub["repo_path"]] == edited
        assert pub["branch"] not in github.branches  # cleaned up

        # Unchanged since the merge: nothing to propose.
        assert (await c.post(f"/v1/sessions/{sid}/publication")).status_code == 409

        # Changed after the merge: a new PR updates the same file.
        again = note_md(sid, "Second thoughts.")
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": again})
        assert r.json()["publication"]["changed"] is True
        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 200, r.text
        second = r.json()["publication"]
        assert second["pr_number"] != pub["pr_number"]
        assert second["repo_path"] == pub["repo_path"]
        await c.post(f"/v1/sessions/{sid}/publication/merge")
        assert github.files("main")[pub["repo_path"]] == again


async def test_close_skip_and_unskip(
    pub_server: Callable[..., ServerHandle], github: FakeGitHub, sm: SM, token: str
) -> None:
    server = pub_server()
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, outbox_of(server), device_id)
    fresh = await finished(sm, outbox_of(server), device_id)
    async with client(server, token) as c:
        pub = (await c.post(f"/v1/sessions/{sid}/publication")).json()["publication"]
        r = await c.post(f"/v1/sessions/{sid}/publication/close")
        assert r.json()["publication"]["state"] == "closed"
        assert github.pulls[pub["pr_number"]].state == "closed"
        assert pub["branch"] not in github.branches

        # Skipping an open PR closes it.
        await c.post(f"/v1/sessions/{sid}/publication")
        reopened = github.pull_for(pub["branch"])
        assert reopened is not None and reopened.state == "open"
        r = await c.post(f"/v1/sessions/{sid}/publication/skip")
        assert r.json()["publication"]["state"] == "skipped"
        assert reopened.state == "closed"
        r = await c.delete(f"/v1/sessions/{sid}/publication/skip")
        assert r.json()["publication"]["state"] == "closed"  # it was published before

        # A note never published goes back to "ready".
        await c.post(f"/v1/sessions/{fresh}/publication/skip")
        q = (await c.get("/v1/publishing")).json()
        assert str(fresh) not in {i["session_id"] for i in q["ready"]}
        assert str(fresh) in {i["session_id"] for i in q["recent"]}
        r = await c.delete(f"/v1/sessions/{fresh}/publication/skip")
        assert r.json()["publication"] is None
        q = (await c.get("/v1/publishing")).json()
        assert str(fresh) in {i["session_id"] for i in q["ready"]}


async def test_who_may_publish(pub_server: Callable[..., ServerHandle], sm: SM, token: str) -> None:
    server = pub_server()
    outbox = outbox_of(server)
    admin = await create_user(sm, f"Admin {uuid.uuid4().hex[:6]}", is_admin=True)
    _, admin_token = await new_device(sm, "admin-mac", user_id=admin.id)
    family_device, family_token = await new_device(sm, "family-phone")
    owner_device = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    admin_device = (await new_device(sm, "admin-mac-2", user_id=admin.id))[0]
    owners = await finished(sm, outbox, owner_device)
    admins = await finished(sm, outbox, admin_device)
    family = await finished(sm, outbox, family_device)

    async with client(server, family_token) as c:
        assert (await c.get("/v1/me")).json()["can_publish"] is False
        d = (await c.get(f"/v1/sessions/{family}")).json()
        assert d["can_publish"] is False
        assert (await c.post(f"/v1/sessions/{family}/publication")).status_code == 403
        assert (await c.post(f"/v1/sessions/{owners}/publication")).status_code == 404
        assert (await c.get("/v1/publishing")).status_code == 403
        # Their own note is still theirs to edit.
        r = await c.put(f"/v1/sessions/{family}/note", json={"markdown": note_md(family, "x")})
        assert r.status_code == 200, r.text

    async with client(server, admin_token) as c:
        assert (await c.get("/v1/me")).json()["can_publish"] is True
        assert (await c.get(f"/v1/sessions/{family}")).json()["can_publish"] is False
        r = await c.post(f"/v1/sessions/{family}/publication")
        assert r.status_code == 409, r.text  # family notes stay out of the vault
        r = await c.post(f"/v1/sessions/{owners}/publication")
        assert r.status_code == 200, r.text
        r = await c.post(f"/v1/sessions/{admins}/publication")
        assert r.status_code == 200, r.text
        # An admin's own notes keep their folder under the publish subdir.
        assert r.json()["publication"]["repo_path"].startswith(f"{SUBDIR}/{admin.notes_subdir}/")
        q = (await c.get("/v1/publishing")).json()
        assert str(family) not in {i["session_id"] for i in q["ready"] + q["open"]}

    async with client(server, token) as c:
        r = await c.put(f"/v1/sessions/{owners}/note", json={"markdown": "# no frontmatter"})
        assert r.status_code == 422


async def test_publishing_is_off_without_a_repo(server: ServerHandle, sm: SM, token: str) -> None:
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, server.settings.data_dir / "outbox", device_id)
    async with client(server, token) as c:
        assert (await c.get("/v1/me")).json()["can_publish"] is False
        assert (await c.get(f"/v1/sessions/{sid}")).json()["can_publish"] is False
        assert (await c.post(f"/v1/sessions/{sid}/publication")).status_code == 503
        assert (await c.get("/v1/publishing")).status_code == 503
        assert (await c.patch("/v1/me", json={"auto_publish": True})).status_code == 403
        # Editing works without publishing.
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": note_md(sid, "edit")})
        assert r.status_code == 200


async def test_merges_and_closes_on_github_are_picked_up(
    pub_server: Callable[..., ServerHandle], github: FakeGitHub, sm: SM, token: str
) -> None:
    server = pub_server()
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    merged = await finished(sm, outbox_of(server), device_id)
    closed = await finished(sm, outbox_of(server), device_id)
    async with client(server, token) as c:
        a = (await c.post(f"/v1/sessions/{merged}/publication")).json()["publication"]
        b = (await c.post(f"/v1/sessions/{closed}/publication")).json()["publication"]
        github.merge_on_github(a["pr_number"])
        github.close_on_github(b["pr_number"])

        r = await c.get(f"/v1/sessions/{merged}/publication")
        assert r.json()["publication"]["state"] == "merged"
        q = (await c.post("/v1/publishing/sync")).json()
        states = {i["session_id"]: i["publication"]["state"] for i in q["recent"]}
        assert states[str(merged)] == "merged" and states[str(closed)] == "closed"


async def test_failures_are_recorded_and_retryable(
    pub_server: Callable[..., ServerHandle], github: FakeGitHub, sm: SM, token: str
) -> None:
    server = pub_server()
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, outbox_of(server), device_id)
    github.fail_next[("POST", "/pulls")] = (403, "Resource not accessible by personal token")
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 502 and "not accessible" in r.json()["detail"]
        pub = (await c.get(f"/v1/sessions/{sid}")).json()["publication"]
        assert pub["state"] == "failed" and "not accessible" in pub["last_error"]
        # The branch was left behind; a retry reuses it.
        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 200, r.text
        assert r.json()["publication"]["last_error"] is None


async def test_publish_all_publishes_every_ready_note(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publish_service, "PUBLISH_ALL_GAP_S", 0.0)
    server = pub_server()
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sids = [await finished(sm, outbox_of(server), device_id) for _ in range(2)]
    async with client(server, token) as c:
        q = (await c.get("/v1/publishing")).json()
        assert {str(s) for s in sids} <= {i["session_id"] for i in q["ready"]}
        r = await c.post("/v1/publishing/publish-all")
        assert r.status_code == 202 and r.json()["status"] in ("started", "running")
        for _ in range(200):
            q = (await c.get("/v1/publishing")).json()
            if not q["publishing_all"]:
                break
            await asyncio.sleep(0.05)
        assert {str(s) for s in sids} <= {i["session_id"] for i in q["open"]}


async def test_auto_publish_opens_a_pr_when_the_note_is_written(
    pub_server: Callable[..., ServerHandle], github: FakeGitHub, sm: SM, token: str
) -> None:
    llm = FakeLLM().queue("FinalNote", FinalNote(title="Auto", summary="S."))
    server = pub_server(llm=llm, finalize_recover_max_age_s=0)
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await make_session(sm, device_id, status="live")
    await add_segments(sm, sid, [(0, 1000, "me", "hello")])
    try:
        async with client(server, token) as c:
            r = await c.patch("/v1/me", json={"auto_publish": True})
            assert r.json()["auto_publish"] is True
            assert (await c.post(f"/v1/sessions/{sid}/end")).status_code == 200
            for _ in range(200):
                d = (await c.get(f"/v1/sessions/{sid}")).json()
                if d["publication"] and d["publication"]["state"] == "open":
                    break
                await asyncio.sleep(0.05)
            assert d["publication"]["state"] == "open", d
            assert github.pulls[d["publication"]["pr_number"]].title == "jarvis-live: Auto"
    finally:
        async with sm() as db:
            await db.execute(
                update(User).where(User.id == OWNER_USER_ID).values(auto_publish=False)
            )
            await db.commit()


async def test_regenerate_rewrites_the_note(
    pub_server: Callable[..., ServerHandle], github: FakeGitHub, sm: SM, token: str
) -> None:
    llm = FakeLLM().queue("FinalNote", FinalNote(title="Better title", summary="Again."))
    server = pub_server(llm=llm, finalize_recover_max_age_s=0)
    outbox = outbox_of(server)
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, outbox, device_id, name="old name.md")
    await add_segments(sm, sid, [(0, 1000, "me", "hello again")])
    async with client(server, token) as c:
        # Not while a PR is open: its branch holds the old note.
        await c.post(f"/v1/sessions/{sid}/publication")
        assert (await c.post(f"/v1/sessions/{sid}/regenerate")).status_code == 409
        await c.post(f"/v1/sessions/{sid}/publication/close")

        r = await c.post(f"/v1/sessions/{sid}/regenerate")
        assert r.status_code == 200, r.text
        for _ in range(200):
            d = (await c.get(f"/v1/sessions/{sid}")).json()
            if d["status"] == "done":
                break
            await asyncio.sleep(0.05)
        assert d["status"] == "done"
        assert d["final_note"]["title"] == "Better title"
        assert "hello again" in d["final_note"]["markdown"]
        assert not (outbox / "old name.md").exists()
        assert d["final_note"]["path"] != "old name.md"
        # The publication keeps its repo path: a republish updates the same file.
        assert d["publication"]["repo_path"] == f"{SUBDIR}/old name.md"


async def test_regenerate_needs_an_llm(server: ServerHandle, sm: SM, token: str) -> None:
    device_id = (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]
    sid = await finished(sm, server.settings.data_dir / "outbox", device_id)
    async with client(server, token) as c:
        assert (await c.get(f"/v1/sessions/{sid}")).json()["can_regenerate"] is False
        assert (await c.post(f"/v1/sessions/{sid}/regenerate")).status_code == 503
