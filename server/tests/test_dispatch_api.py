"""Dispatch through the API: the real app and Postgres, a fake Kubernetes and token source."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete

from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, WorkItemDispatch
from jarvis_live.dispatch.service import dispatch_config_problems
from tests.conftest import ServerHandle
from tests.fake_kube import FakeKube, FakeTokens
from tests.work_item_helpers import SETTINGS, SM, owner_with_session, seed_item, web_client

DISPATCH = {**SETTINGS, "dispatch_enabled": True, "git_sha": "abc1234", "dispatch_poll_s": 0.05}


@pytest.fixture
async def kube(sm: SM) -> FakeKube:
    async with sm() as db:  # the loop is deployment-wide: no runs left by other tests
        await db.execute(delete(WorkItemDispatch))
        await db.commit()
    return FakeKube()


@pytest.fixture
def dserver(make_server: Callable[..., ServerHandle], kube: FakeKube) -> ServerHandle:
    return make_server(kube=kube, app_tokens=FakeTokens(), **DISPATCH)


async def until(cond: Callable[[], Any], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await cond():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.05)


async def test_dispatch_runs_and_the_outcome_shows_on_the_item(
    dserver: ServerHandle, sm: SM, kube: FakeKube
) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(dserver, sm, OWNER_USER_ID) as web:
        page = (await web.get(f"/v1/sessions/{sid}/work-items")).json()
        assert page["dispatch_enabled"] is True and page["items"][0]["dispatch"] is None
        await web.post(f"/v1/work-items/{item}/approve")
        r = await web.post(f"/v1/work-items/{item}/dispatch")
        assert r.status_code == 202, r.text
        assert r.json()["dispatch"]["state"] in ("queued", "running")

        async def started() -> bool:
            return bool(kube.jobs)

        await until(started)
        (job,) = kube.jobs
        kube.report(job, outcome="pr_opened", pr_url="https://github.com/o/r/pull/9", pr_number=9)

        async def finished() -> bool:
            d = (await web.get(f"/v1/work-items/{item}")).json()["dispatch"]
            return bool(d and d["state"] == "pr_opened")

        await until(finished)
        d = (await web.get(f"/v1/sessions/{sid}/work-items")).json()["items"][0]["dispatch"]
        assert d["pr_url"] == "https://github.com/o/r/pull/9" and d["attempt"] == 1


async def test_cancel_a_run(dserver: ServerHandle, sm: SM, kube: FakeKube) -> None:
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(dserver, sm, OWNER_USER_ID) as web:
        await web.post(f"/v1/work-items/{item}/approve")
        await web.post(f"/v1/work-items/{item}/dispatch")
        r = await web.post(f"/v1/work-items/{item}/dispatch/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["dispatch"]["state"] == "cancelled"
        assert (await web.post(f"/v1/work-items/{item}/dispatch/cancel")).status_code == 409


async def test_cancel_without_dispatch_is_503(
    make_server: Callable[..., ServerHandle], sm: SM
) -> None:
    server = make_server(**SETTINGS)
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    item = await seed_item(sm, sid, OWNER_USER_ID)
    async with await web_client(server, sm, OWNER_USER_ID) as web:
        assert (await web.post(f"/v1/work-items/{item}/dispatch/cancel")).status_code == 503


def test_dispatch_needs_its_configuration(tmp_path: Path) -> None:
    assert dispatch_config_problems(Settings(dispatch_enabled=True)) == [
        "work items are off",
        "no runner image (JARVIS_LIVE_DISPATCH_IMAGE or GIT_SHA)",
        "no GitHub App (JARVIS_LIVE_GITHUB_APP_ID, _INSTALLATION_ID, _KEY_FILE)",
    ]
    key = tmp_path / "app.pem"
    key.write_text("k")
    ok = Settings(
        dispatch_enabled=True,
        work_items_enabled=True,
        git_sha="abc",
        github_app_id="1",
        github_app_installation_id="2",
        github_app_key_file=key,
    )
    assert dispatch_config_problems(ok) == []


async def test_misconfigured_dispatch_stays_off_and_the_server_still_starts(
    make_server: Callable[..., ServerHandle], sm: SM, tmp_path: Path
) -> None:
    """Lab enables dispatch before its 1Password items exist: no crash loop, dispatch off."""
    server = make_server(
        **DISPATCH,
        github_app_id="1",
        github_app_installation_id="2",
        github_app_key_file=tmp_path / "not-mounted-yet.pem",
    )
    sid, _ = await owner_with_session(sm, OWNER_USER_ID)
    async with await web_client(server, sm, OWNER_USER_ID) as web:
        page = (await web.get(f"/v1/sessions/{sid}/work-items")).json()
        assert page["enabled"] is True and page["dispatch_enabled"] is False


def test_a_missing_app_key_file_is_a_problem(tmp_path: Path) -> None:
    cfg = Settings(
        dispatch_enabled=True,
        work_items_enabled=True,
        git_sha="abc",
        github_app_id="1",
        github_app_installation_id="2",
        github_app_key_file=tmp_path / "absent.pem",
    )
    assert dispatch_config_problems(cfg) == [
        f"the GitHub App key {tmp_path / 'absent.pem'} is missing"
    ]
