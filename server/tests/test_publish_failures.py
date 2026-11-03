"""Publishing to the vault when something is wrong: the note, the session, or GitHub.

The real app against the fake GitHub and Postgres, as in test_publish.py. Every refusal here
must leave the vault untouched and say why."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import update

from jarvis_live.db.models import OWNER_USER_ID, CopilotItem
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.publish.service import MAX_NOTE_BYTES
from tests.conftest import ServerHandle
from tests.fake_github import FakeGitHub, _sha
from tests.fakes import make_session, new_device
from tests.test_publish import SM, client, finished, note_md, outbox_of


@pytest.fixture
def pub_server(
    make_server: Callable[..., ServerHandle], publish_settings: dict[str, Any]
) -> Callable[..., ServerHandle]:
    return lambda **kw: make_server(**{**publish_settings, **kw})


@pytest.fixture
async def owner_device(sm: SM) -> uuid.UUID:
    return (await new_device(sm, "mac", user_id=OWNER_USER_ID))[0]


async def _open(server: ServerHandle, token: str, sid: uuid.UUID) -> dict:  # type: ignore[type-arg]
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication")
    assert r.status_code == 200, r.text
    pub: dict = r.json()["publication"]  # type: ignore[type-arg]
    return pub


# --- the note -------------------------------------------------------------------------------


async def test_a_note_too_large_for_the_vault_is_refused_before_it_is_saved(
    pub_server: Callable[..., ServerHandle], sm: SM, token: str, owner_device: uuid.UUID
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    # Under the API's limit in characters, over the vault's in bytes.
    huge = note_md(sid, "é" * (MAX_NOTE_BYTES // 2 + 1))
    assert len(huge) < MAX_NOTE_BYTES < len(huge.encode())
    async with client(server, token) as c:
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": huge})
        assert r.status_code == 413
        assert (await c.get(f"/v1/sessions/{sid}")).json()["final_note"]["markdown"] == note_md(sid)


async def test_a_note_still_being_written_cannot_be_edited_or_published(
    pub_server: Callable[..., ServerHandle], sm: SM, token: str, owner_device: uuid.UUID
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    async with sm() as db:
        await db.execute(update(SessionRow).where(SessionRow.id == sid).values(status="finalizing"))
        await db.commit()
    async with client(server, token) as c:
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": note_md(sid, "early")})
        assert r.status_code == 409 and "being written" in r.json()["detail"]
        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 409 and "not finished" in r.json()["detail"]


async def test_a_session_without_a_note_has_nothing_to_edit_or_publish(
    pub_server: Callable[..., ServerHandle], sm: SM, token: str, owner_device: uuid.UUID
) -> None:
    server = pub_server()
    sid = await make_session(sm, owner_device, status="done")
    async with client(server, token) as c:
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": note_md(sid)})
        assert r.status_code == 409 and "no note" in r.json()["detail"]
        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 409 and "no note" in r.json()["detail"]


async def test_a_note_removed_from_the_outbox_is_not_published(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
) -> None:
    server = pub_server()
    outbox = outbox_of(server)
    sid = await finished(sm, outbox, owner_device)
    (note,) = outbox.glob("*.md")
    note.unlink()
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication")
        assert r.status_code == 409 and "no longer in the outbox" in r.json()["detail"]
        # An edit is the whole note: saving it writes the note back.
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": note_md(sid, "Back.")})
        assert r.status_code == 200
    assert note.read_text() == note_md(sid, "Back.")
    assert github.pulls == {}


@pytest.mark.parametrize("stored", ["../escape.md", "/etc/escape.md", "a/b/c.md"])
async def test_a_stored_note_path_outside_the_outbox_is_never_written(
    pub_server: Callable[..., ServerHandle],
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
    stored: str,
) -> None:
    server = pub_server()
    outbox = outbox_of(server)
    outbox.mkdir(parents=True, exist_ok=True)
    sid = await make_session(sm, owner_device, status="done")
    async with sm() as db:
        db.add(
            CopilotItem(session_id=sid, kind="final_note", payload={"path": stored, "title": "x"})
        )
        await db.commit()
    async with client(server, token) as c:
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": note_md(sid)})
        assert r.status_code == 409 and "no longer in the outbox" in r.json()["detail"]
        assert (await c.post(f"/v1/sessions/{sid}/publication")).status_code == 409
    assert not (outbox.parent / "escape.md").exists()
    assert not (outbox / "a").exists()


async def test_a_session_that_does_not_exist_is_not_found(
    pub_server: Callable[..., ServerHandle], token: str
) -> None:
    server = pub_server()
    sid = uuid.uuid4()
    async with client(server, token) as c:
        assert (await c.post(f"/v1/sessions/{sid}/publication")).status_code == 404


# --- GitHub ---------------------------------------------------------------------------------


async def test_an_edit_saved_locally_but_not_pushed_says_so_and_is_recorded(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
) -> None:
    server = pub_server()
    outbox = outbox_of(server)
    sid = await finished(sm, outbox, owner_device)
    pub = await _open(server, token, sid)
    github.fail_next[("PUT", pub["repo_path"])] = (500, "GitHub is having a moment")
    edited = note_md(sid, "Edited while GitHub was down.")
    async with client(server, token) as c:
        r = await c.put(f"/v1/sessions/{sid}/note", json={"markdown": edited})
        assert r.status_code == 502 and "saved, but pushing the edit failed" in r.json()["detail"]
        detail = (await c.get(f"/v1/sessions/{sid}")).json()
    assert detail["final_note"]["markdown"] == edited  # the outbox has it
    assert "pushing the edit failed" in detail["publication"]["last_error"]
    assert github.files(pub["branch"])[pub["repo_path"]] == note_md(sid)  # the PR does not


async def test_a_pull_request_changed_on_github_is_not_merged_blind(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    pub = await _open(server, token, sid)
    # Someone pushes to the branch on github.com.
    sneaky = _sha("someone else", pub["branch"])
    github.commits[sneaky] = {**github.files(pub["branch"]), pub["repo_path"]: "rewritten\n"}
    github.branches[pub["branch"]] = sneaky
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication/merge")
        assert r.status_code == 409 and "review it there" in r.json()["detail"]
    assert pub["repo_path"] not in github.files("main")


async def test_a_merge_github_refuses_is_reported_and_the_pull_request_stays_open(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    pub = await _open(server, token, sid)
    github.fail_next[("PUT", f"/pulls/{pub['pr_number']}/merge")] = (409, "merge conflict")
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication/merge")
        assert r.status_code == 409 and "merge conflict" in r.json()["detail"]
        assert (await c.get(f"/v1/sessions/{sid}")).json()["publication"]["state"] == "open"
        assert pub["branch"] in github.branches
        github.fail_next[("PUT", f"/pulls/{pub['pr_number']}/merge")] = (500, "boom")
        assert (await c.post(f"/v1/sessions/{sid}/publication/merge")).status_code == 502


async def test_merging_a_pull_request_merged_on_github_records_it(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    pub = await _open(server, token, sid)
    github.merge_on_github(pub["pr_number"])
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication/merge")
        assert r.status_code == 200, r.text
        assert r.json()["publication"]["state"] == "merged"
    merges = [c for c in github.calls if c[0] == "PUT" and c[1].endswith("/merge")]
    assert merges == []  # nothing was merged twice


async def test_github_unreachable_while_merging_or_closing_is_a_502(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    pub = await _open(server, token, sid)
    number = pub["pr_number"]
    async with client(server, token) as c:
        github.fail_next[("GET", f"/pulls/{number}")] = (502, "Bad gateway")
        assert (await c.post(f"/v1/sessions/{sid}/publication/merge")).status_code == 502
        github.fail_next[("PATCH", f"/pulls/{number}")] = (502, "Bad gateway")
        assert (await c.post(f"/v1/sessions/{sid}/publication/close")).status_code == 502
        assert (await c.get(f"/v1/sessions/{sid}")).json()["publication"]["state"] == "open"


async def test_a_branch_that_cannot_be_deleted_does_not_fail_the_merge(
    pub_server: Callable[..., ServerHandle],
    github: FakeGitHub,
    sm: SM,
    token: str,
    owner_device: uuid.UUID,
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    pub = await _open(server, token, sid)
    github.fail_next[("DELETE", pub["branch"])] = (422, "Reference does not exist")
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication/merge")
    assert r.status_code == 200 and r.json()["publication"]["state"] == "merged"


# --- what there is to act on ----------------------------------------------------------------


async def test_merge_close_and_unskip_need_something_to_act_on(
    pub_server: Callable[..., ServerHandle], sm: SM, token: str, owner_device: uuid.UUID
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    async with client(server, token) as c:
        r = await c.post(f"/v1/sessions/{sid}/publication/merge")
        assert r.status_code == 409 and "no open pull request" in r.json()["detail"]
        assert (await c.post(f"/v1/sessions/{sid}/publication/close")).status_code == 409
        r = await c.delete(f"/v1/sessions/{sid}/publication/skip")
        assert r.status_code == 409 and "not skipped" in r.json()["detail"]


async def test_a_merged_note_cannot_be_skipped(
    pub_server: Callable[..., ServerHandle], sm: SM, token: str, owner_device: uuid.UUID
) -> None:
    server = pub_server()
    sid = await finished(sm, outbox_of(server), owner_device)
    await _open(server, token, sid)
    async with client(server, token) as c:
        assert (await c.post(f"/v1/sessions/{sid}/publication/merge")).status_code == 200
        r = await c.post(f"/v1/sessions/{sid}/publication/skip")
        assert r.status_code == 409 and "already merged" in r.json()["detail"]
