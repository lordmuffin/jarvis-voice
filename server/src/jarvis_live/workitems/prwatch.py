"""Closing work items whose pull request merged on Forgejo.

An approved prompt usually runs outside this server (copied into Claude Code), so nothing here
knows a PR's number. Each pass lists a repo's recently closed PRs and matches them to its
approved items: by the branch the prompt names, the branches the item's runs used, or the
``Jarvis-Work-Item`` line the prompt asks for in the description (Claude Code often keeps a
branch of its own). Reading only. A PR closed without merging leaves the item open, and so
does a repo not in ``work_forgejo_repos``: those close by the owner's Mark Done."""

import asyncio
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import WorkItem, WorkItemDispatch
from jarvis_live.workitems.service import WorkItemService
from jarvis_live.workitems.template import branch_name, pr_marker

log = logging.getLogger(__name__)

TIMEOUT_S = 30.0
# One page, newest first: a pass every few minutes never falls this far behind.
PAGE = 50


def _matches(pr: dict[str, Any], heads: set[str], marker: str) -> bool:
    # ``head.label`` is the branch name; ``head.ref`` is often refs/pull/N/head instead.
    head = pr.get("head") or {}
    return bool({head.get("label"), head.get("ref")} & heads) or marker in (pr.get("body") or "")


class PrWatch:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        work: WorkItemService,
        client: httpx.AsyncClient,
    ) -> None:
        assert settings.forgejo_token_file is not None
        self._url = settings.forgejo_url.rstrip("/")
        self._token_file = settings.forgejo_token_file
        self._repos = settings.work_forgejo_repo_map
        self._sm = sessionmaker
        self._work = work
        self._client = client

    async def once(self) -> None:
        async with self._sm() as db:
            items = list(
                (
                    await db.execute(
                        select(WorkItem).where(
                            WorkItem.state == "approved", WorkItem.repo.in_(self._repos)
                        )
                    )
                )
                .scalars()
                .all()
            )
            runs = await db.execute(
                select(WorkItemDispatch.work_item_id, WorkItemDispatch.branch).where(
                    WorkItemDispatch.work_item_id.in_([i.id for i in items])
                )
            )
            branches: dict[uuid.UUID, set[str]] = {}
            for item_id, branch in runs.all():
                branches.setdefault(item_id, set()).add(branch)
        if not items:
            return
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()  # a rotation is picked up
        except OSError as e:
            log.warning("cannot read the Forgejo token: %s", e)
            return
        for repo in {i.repo for i in items if i.repo}:
            try:
                merged = await self._merged_prs(self._repos[repo], token)
            except httpx.HTTPError as e:
                log.warning("listing the PRs of %s failed: %r", self._repos[repo], e)
                continue
            for item in (i for i in items if i.repo == repo):
                heads = {
                    branch_name(str(item.session_id), item.ref, item.title),
                    *branches.get(item.id, ()),
                }
                for pr in merged:
                    url = str(pr["html_url"])
                    if url != item.merged_pr_url and _matches(pr, heads, pr_marker(str(item.id))):
                        await self._work.pr_merged(item.id, url)
                        break

    async def run_loop(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("work item PR pass failed")

    async def _merged_prs(self, repo: str, token: str) -> list[dict[str, Any]]:
        r = await self._client.get(
            f"{self._url}/api/v1/repos/{repo}/pulls",
            params={"state": "closed", "sort": "recentupdate", "limit": str(PAGE)},
            headers={"Authorization": f"token {token}", "Accept": "application/json"},
            timeout=TIMEOUT_S,
        )
        r.raise_for_status()
        return [pr for pr in r.json() if pr.get("merged")]
