"""Closing work items when their PR merges: the watcher against a real Postgres and a fake
Forgejo."""

import asyncio
import json
import threading
import uuid
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, WorkItem, WorkItemDispatch, WorkItemEvent
from jarvis_live.workitems.dispatch import Actor
from jarvis_live.workitems.prwatch import PrWatch
from jarvis_live.workitems.service import WorkItemService
from jarvis_live.workitems.template import branch_name
from tests.conftest import ServerHandle
from tests.work_item_helpers import REPOS, SM, owner_with_session, seed_item

HOME = "lordmuffin/np-home-homelab"  # the name items carry
FORGE = "lordmuffin/homelab-gitops"  # the same repo on Forgejo
OWNER = Actor(OWNER_USER_ID, "web")


def pull(
    number: int, branch: str = "other", *, merged: bool = True, body: str = ""
) -> dict[str, Any]:
    """A closed PR as Forgejo lists it: ``ref`` turns into refs/pull/N/head once the branch is
    deleted, and ``label`` keeps the branch name."""
    return {
        "number": number,
        "html_url": f"https://git.test/{FORGE}/pulls/{number}",
        "merged": merged,
        "merged_at": "2026-10-30T12:00:00Z" if merged else None,
        "body": body,
        "head": {"label": branch, "ref": f"refs/pull/{number}/head"},
    }


class Forgejo:
    def __init__(self) -> None:
        self.pulls: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[httpx.Request] = []
        self.status = 200

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "nope"})
        repo = req.url.path.removeprefix("/api/v1/repos/").removesuffix("/pulls")
        return httpx.Response(200, json=self.pulls.get(repo, []))


class Rig:
    def __init__(self, sm: SM, tmp_path: Path) -> None:
        self.sm = sm
        self.forge = Forgejo()
        self.token = tmp_path / "token"
        self.token.write_text("tok\n")
        cfg = Settings(
            work_items_enabled=True,
            work_repos=REPOS,
            forgejo_url="https://git.test/",
            forgejo_token_file=self.token,
            work_forgejo_repos=f"{HOME}  {FORGE}",
        )
        self.work = WorkItemService(cfg, sm)
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.forge.handler))
        self.watch = PrWatch(cfg, sm, self.work, client)

    async def approved(self, **kw: Any) -> uuid.UUID:
        sid, _ = await owner_with_session(self.sm, OWNER_USER_ID)
        item = await seed_item(self.sm, sid, OWNER_USER_ID, repo=kw.pop("repo", HOME), **kw)
        await self.work.approve(item, OWNER)
        return item

    async def branch(self, item_id: uuid.UUID) -> str:
        row = await self.row(item_id)
        return branch_name(str(row.session_id), row.ref, row.title)

    async def row(self, item_id: uuid.UUID) -> WorkItem:
        async with self.sm() as db:
            row = await db.get(WorkItem, item_id)
            assert row is not None
            return row

    async def events(self, item_id: uuid.UUID) -> list[WorkItemEvent]:
        async with self.sm() as db:
            rows = await db.execute(
                select(WorkItemEvent)
                .where(WorkItemEvent.work_item_id == item_id)
                .order_by(WorkItemEvent.id)
            )
            return list(rows.scalars().all())


async def retire_approved(sm: SM) -> None:
    """The watcher is deployment-wide: approved items other tests left would be matched too."""
    async with sm() as db:
        approved = await db.execute(select(WorkItem).where(WorkItem.state == "approved"))
        for row in approved.scalars():
            row.state = "discarded"
        await db.commit()


@pytest.fixture
async def rig(sm: SM, tmp_path: Path) -> Rig:
    await retire_approved(sm)
    return Rig(sm, tmp_path)


async def test_a_merged_pr_on_the_items_branch_closes_it(rig: Rig) -> None:
    item = await rig.approved()
    rig.forge.pulls[FORGE] = [pull(7, await rig.branch(item))]

    await rig.watch.once()

    row = await rig.row(item)
    url = f"https://git.test/{FORGE}/pulls/7"
    assert (row.state, row.merged_pr_url) == ("done", url)
    assert row.done_at is not None
    done = (await rig.events(item))[-1]
    assert (done.event, done.actor_kind, done.payload) == ("done", "system", {"pr_url": url})
    req = rig.forge.requests[0]
    assert req.url.path == f"/api/v1/repos/{FORGE}/pulls"
    assert req.url.params["state"] == "closed" and req.headers["authorization"] == "token tok"


async def test_a_merged_pr_naming_the_item_in_its_description_closes_it(rig: Rig) -> None:
    """Claude Code often keeps its own branch: the prompt's marker line finds those PRs."""
    item = await rig.approved()
    rig.forge.pulls[FORGE] = [pull(9, body=f"Moves storage.\n\nJarvis-Work-Item: {item}\n")]

    await rig.watch.once()

    assert (await rig.row(item)).state == "done"


async def test_a_pr_closed_without_merging_leaves_the_item_open(rig: Rig) -> None:
    item = await rig.approved()
    rig.forge.pulls[FORGE] = [pull(7, await rig.branch(item), merged=False)]

    await rig.watch.once()

    assert (await rig.row(item)).state == "approved"


async def test_a_runs_own_branch_is_checked_too(rig: Rig) -> None:
    item = await rig.approved()
    prompt_sha = (await rig.row(item)).prompt_sha256
    assert prompt_sha
    async with rig.sm() as db:
        db.add(
            WorkItemDispatch(
                id=uuid.uuid4(),
                work_item_id=item,
                attempt=2,
                state="pr_opened",
                repo=HOME,
                branch="claude/run-branch-2",
                prompt_sha256=prompt_sha,
                requested_by=OWNER_USER_ID,
            )
        )
        await db.commit()
    rig.forge.pulls[FORGE] = [pull(7, "claude/run-branch-2")]

    await rig.watch.once()

    assert (await rig.row(item)).state == "done"


async def test_drafts_are_not_matched(rig: Rig) -> None:
    sid, _ = await owner_with_session(rig.sm, OWNER_USER_ID)
    item = await seed_item(rig.sm, sid, OWNER_USER_ID, repo=HOME)
    rig.forge.pulls[FORGE] = [pull(7, await rig.branch(item))]

    await rig.watch.once()

    assert (await rig.row(item)).state == "draft"


async def test_a_repo_not_on_forgejo_is_never_looked_up(rig: Rig) -> None:
    item = await rig.approved(repo="lordmuffin/jarvis-voice")

    await rig.watch.once()

    assert rig.forge.requests == []
    assert (await rig.row(item)).state == "approved"


async def test_a_reopened_item_stays_open_until_a_different_pr_merges(rig: Rig) -> None:
    item = await rig.approved()
    branch = await rig.branch(item)
    rig.forge.pulls[FORGE] = [pull(7, branch)]
    await rig.watch.once()
    await rig.work.reopen(item, OWNER)

    await rig.watch.once()
    assert (await rig.row(item)).state == "approved"

    rig.forge.pulls[FORGE].insert(0, pull(8, branch))
    await rig.watch.once()
    row = await rig.row(item)
    assert (row.state, row.merged_pr_url) == ("done", f"https://git.test/{FORGE}/pulls/8")


async def test_forgejo_failing_leaves_items_for_the_next_pass(rig: Rig) -> None:
    item = await rig.approved()
    rig.forge.pulls[FORGE] = [pull(7, await rig.branch(item))]
    rig.forge.status = 403

    await rig.watch.once()
    assert (await rig.row(item)).state == "approved"

    rig.forge.status = 200
    await rig.watch.once()
    assert (await rig.row(item)).state == "done"


async def test_a_missing_token_skips_the_pass_and_a_rotated_one_is_picked_up(rig: Rig) -> None:
    item = await rig.approved()
    rig.forge.pulls[FORGE] = [pull(7, await rig.branch(item))]
    rig.token.unlink()

    await rig.watch.once()
    assert rig.forge.requests == []

    rig.token.write_text("rotated\n")
    await rig.watch.once()
    assert rig.forge.requests[0].headers["authorization"] == "token rotated"
    assert (await rig.row(item)).state == "done"


@pytest.fixture
def forgejo_http() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """A real HTTP server, for the running app."""
    pulls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps(pulls).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", pulls
    server.shutdown()


async def test_the_running_app_closes_items_when_configured(
    make_server: Callable[..., ServerHandle],
    forgejo_http: tuple[str, list[dict[str, Any]]],
    sm: SM,
    tmp_path: Path,
) -> None:
    await retire_approved(sm)
    url, pulls = forgejo_http
    token = tmp_path / "forgejo-token"
    token.write_text("tok")
    server = make_server(
        work_items_enabled=True,
        work_repos=REPOS,
        public_base_url="",
        forgejo_url=url,
        forgejo_token_file=token,
        work_forgejo_repos=f"{HOME}  {FORGE}",
        work_pr_poll_s=0.05,
    )
    work = WorkItemService(server.settings, sm)
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID, repo=HOME)
    await work.approve(item, OWNER)
    pulls.append(pull(7, body=f"Jarvis-Work-Item: {item}"))

    async with asyncio.timeout(5):
        while (await work.get(item, OWNER_USER_ID)).state != "done":
            await asyncio.sleep(0.05)
