"""Running approved work items as headless Claude Code, one Kubernetes Job each.

``dispatch`` (called by the work item gate, after it passed) only queues a run. A single loop
then starts queued runs while fewer than ``dispatch_max_concurrent`` are running (the runs
share one Claude subscription), reads finished runs' outcomes back, refreshes the GitHub token
of long runs, and tells the owner how each run ended. Runs and their outcomes are rows in
``work_item_dispatches``; every step is in ``work_item_events``.

A queued run starts only if its item still carries the approved prompt it was queued with:
an item unapproved or edited in between never runs."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jarvis_live.config import Settings
from jarvis_live.db.models import Session as SessionRow
from jarvis_live.db.models import WorkItem, WorkItemDispatch
from jarvis_live.dispatch.githubapp import TokenError
from jarvis_live.dispatch.jobspec import RunSpec, job_manifest, secret_manifest
from jarvis_live.dispatch.kube import KubeError, RunState, interpret
from jarvis_live.notify.gotify import Notifier
from jarvis_live.workitems.dispatch import Actor, sha256
from jarvis_live.workitems.rows import log_event
from jarvis_live.workitems.service import WorkItemError
from jarvis_live.workitems.template import branch_name, session_url

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
TOKEN_REFRESH = timedelta(minutes=30)  # installation tokens last an hour


class Kube(Protocol):
    async def create_secret(self, manifest: dict[str, Any]) -> dict[str, Any]: ...
    async def create_job(self, manifest: dict[str, Any]) -> dict[str, Any]: ...
    async def patch_secret(self, name: str, patch: dict[str, Any]) -> None: ...
    async def get_job(self, name: str) -> dict[str, Any] | None: ...
    async def list_pods(self, selector: str) -> list[dict[str, Any]]: ...
    async def delete_job(self, name: str) -> None: ...
    async def delete_secret(self, name: str) -> None: ...


class Tokens(Protocol):
    async def mint(self, repo: str) -> str: ...


def _node_selector(text: str) -> dict[str, str]:
    pairs = (p.split("=", 1) for p in text.split(",") if "=" in p)
    return {k.strip(): v.strip() for k, v in pairs}


def dispatch_config_problems(cfg: Settings, *, own_tokens: bool = True) -> list[str]:
    """What is missing before dispatch can run; checked at startup when it is enabled.
    ``own_tokens=False``: a token source is supplied (tests), so no App is needed."""
    problems = []
    if not cfg.work_items_enabled:
        problems.append("work items are off")
    if not cfg.dispatch_runner_image:
        problems.append("no runner image (JARVIS_LIVE_DISPATCH_IMAGE or GIT_SHA)")
    app = cfg.github_app_id and cfg.github_app_installation_id and cfg.github_app_key_file
    if own_tokens and not app:
        problems.append("no GitHub App (JARVIS_LIVE_GITHUB_APP_ID, _INSTALLATION_ID, _KEY_FILE)")
    return problems


async def latest_runs(
    db: AsyncSession, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, WorkItemDispatch]:
    """Each item's most recent run."""
    if not item_ids:
        return {}
    rows = (
        await db.execute(
            select(WorkItemDispatch)
            .where(WorkItemDispatch.work_item_id.in_(item_ids))
            .order_by(WorkItemDispatch.attempt)
        )
    ).scalars()
    return {r.work_item_id: r for r in rows}  # later attempts overwrite earlier ones


class DispatchService:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: "async_sessionmaker[AsyncSession]",
        kube: Kube,
        tokens: Tokens,
        notifier: Notifier,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._cfg = settings
        self._sm = sessionmaker
        self._kube = kube
        self._tokens = tokens
        self._notifier = notifier
        self._now = now or (lambda: datetime.now(UTC))
        self._wake = asyncio.Event()

    # --- the Dispatcher protocol (behind the work item gate) --------------------------------

    async def dispatch(
        self,
        item_id: uuid.UUID,
        *,
        repo: str,
        prompt: str,
        prompt_sha256: str,
        actor_user_id: uuid.UUID,
    ) -> None:
        async with self._sm() as db, db.begin():
            item = await db.get(WorkItem, item_id, with_for_update=True)
            assert item is not None
            runs = list(
                (
                    await db.execute(
                        select(WorkItemDispatch).where(WorkItemDispatch.work_item_id == item_id)
                    )
                )
                .scalars()
                .all()
            )
            if any(r.state in ACTIVE for r in runs):
                raise WorkItemError(409, "this work item is already queued or running")
            attempt = max((r.attempt for r in runs), default=0) + 1
            branch = branch_name(str(item.session_id), item.ref, item.title)
            db.add(
                WorkItemDispatch(
                    id=uuid.uuid4(),
                    work_item_id=item_id,
                    attempt=attempt,
                    state="queued",
                    repo=repo,
                    branch=branch if attempt == 1 else f"{branch}-{attempt}",
                    prompt_sha256=prompt_sha256,
                    requested_by=actor_user_id,
                )
            )
        self._wake.set()

    async def cancel(self, item_id: uuid.UUID, actor: Actor) -> None:
        async with self._sm() as db, db.begin():
            item = await db.get(WorkItem, item_id)
            if item is None or item.user_id != actor.user_id:
                raise WorkItemError(404, "work item not found")
            run = (
                await db.execute(
                    select(WorkItemDispatch)
                    .where(
                        WorkItemDispatch.work_item_id == item_id,
                        WorkItemDispatch.state.in_(ACTIVE),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise WorkItemError(409, "nothing to cancel")
            if run.job_name:
                await self._kube.delete_job(run.job_name)
            run.state, run.finished_at = "cancelled", self._now()
            log_event(
                db,
                item_id,
                "dispatch_cancelled",
                actor_user_id=actor.user_id,
                actor_kind=actor.kind,
                payload={"dispatch_id": str(run.id), "job": run.job_name},
            )

    # --- the loop ---------------------------------------------------------------------------

    async def run_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("dispatch tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), self._cfg.dispatch_poll_s)
            except TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        async with self._sm() as db:
            running = list(
                (
                    await db.execute(
                        select(WorkItemDispatch).where(WorkItemDispatch.state == "running")
                    )
                )
                .scalars()
                .all()
            )
        for run in running:
            await self._check(run)
        async with self._sm() as db:
            busy = (
                await db.execute(
                    select(func.count())
                    .select_from(WorkItemDispatch)
                    .where(WorkItemDispatch.state == "running")
                )
            ).scalar_one()
            queued = list(
                (
                    await db.execute(
                        select(WorkItemDispatch.id)
                        .where(WorkItemDispatch.state == "queued")
                        .order_by(WorkItemDispatch.created_at, WorkItemDispatch.id)
                    )
                )
                .scalars()
                .all()
            )
        for run_id in queued[: max(0, self._cfg.dispatch_max_concurrent - busy)]:
            await self._start(run_id)

    # --- one run ----------------------------------------------------------------------------

    async def _start(self, run_id: uuid.UUID) -> None:
        async with self._sm() as db, db.begin():
            run = await db.get(WorkItemDispatch, run_id, with_for_update=True)
            if run is None or run.state != "queued":
                return
            item = await db.get(WorkItem, run.work_item_id)
            assert item is not None
            if (
                item.state != "approved"
                or not item.prompt_md
                or sha256(item.prompt_md) != run.prompt_sha256
            ):
                self._end(
                    db,
                    run,
                    RunState(True, "failed", error="the work item changed after it was queued"),
                )
                return
            spec = RunSpec(
                dispatch_id=run.id,
                repo=run.repo,
                branch=run.branch,
                title=item.title,
                item_url=session_url(self._cfg, str(item.session_id)),
                image=self._cfg.dispatch_runner_image,
                model=self._cfg.dispatch_model,
                deadline_s=self._cfg.dispatch_deadline_s,
                oauth_secret=self._cfg.dispatch_oauth_secret,
                mirror_pvc=self._cfg.dispatch_mirror_pvc,
                postgres_image=self._cfg.dispatch_postgres_image,
                node_selector=_node_selector(self._cfg.dispatch_node_selector),
            )
            try:
                token = await self._tokens.mint(run.repo)
            except TokenError as e:
                self._end(db, run, RunState(True, "failed", error=f"no GitHub token: {e}"))
                return
            name = spec.job_name
            try:
                await self._kube.create_secret(
                    secret_manifest(spec, token=token, prompt=item.prompt_md)
                )
                job = await self._kube.create_job(job_manifest(spec))
                owner = {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "name": name,
                    "uid": job["metadata"]["uid"],
                }
                await self._kube.patch_secret(name, {"metadata": {"ownerReferences": [owner]}})
            except KubeError as e:
                await self._delete_secret(name)
                self._end(db, run, RunState(True, "failed", error=str(e)))
                return
            now = self._now()
            run.state, run.job_name = "running", name
            run.started_at = run.token_refreshed_at = now
            log_event(
                db,
                run.work_item_id,
                "dispatch_started",
                payload={
                    "dispatch_id": str(run.id),
                    "attempt": run.attempt,
                    "job": name,
                    "repo": run.repo,
                    "branch": run.branch,
                    "image": spec.image,
                    "model": spec.model,
                },
            )

    async def _check(self, run: WorkItemDispatch) -> None:
        assert run.job_name is not None
        try:
            job = await self._kube.get_job(run.job_name)
            pods = await self._kube.list_pods(f"job-name={run.job_name}")
        except KubeError:
            log.exception("cannot read job %s", run.job_name)
            return
        state = interpret(job, pods)
        if not state.done:
            await self._refresh(run)
            return
        async with self._sm() as db, db.begin():
            fresh = await db.get(WorkItemDispatch, run.id, with_for_update=True)
            if fresh is None or fresh.state != "running":
                return  # cancelled meanwhile
            self._end(db, fresh, state)
            item = await db.get(WorkItem, fresh.work_item_id)
            session = await db.get(SessionRow, item.session_id) if item else None
        await self._tell(fresh, item, session)

    async def _refresh(self, run: WorkItemDispatch) -> None:
        if run.token_refreshed_at and self._now() - run.token_refreshed_at < TOKEN_REFRESH:
            return
        assert run.job_name is not None
        try:
            token = await self._tokens.mint(run.repo)
            await self._kube.patch_secret(run.job_name, {"stringData": {"token": token}})
        except (TokenError, KubeError):
            log.exception("token refresh failed for %s", run.job_name)
            return
        async with self._sm() as db, db.begin():
            fresh = await db.get(WorkItemDispatch, run.id, with_for_update=True)
            if fresh is not None:
                fresh.token_refreshed_at = self._now()

    def _end(self, db: AsyncSession, run: WorkItemDispatch, state: RunState) -> None:
        run.state = state.outcome or "failed"
        run.pr_url, run.pr_number = state.pr_url, state.pr_number
        run.summary, run.error = state.summary, state.error
        run.finished_at = self._now()
        if state.branch:
            run.branch = state.branch
        log_event(
            db,
            run.work_item_id,
            "dispatch_finished",
            payload={
                "dispatch_id": str(run.id),
                "attempt": run.attempt,
                "outcome": run.state,
                "job": run.job_name,
                "repo": run.repo,
                "branch": run.branch,
                "pr_url": run.pr_url,
                "summary": run.summary,
                "error": run.error,
            },
        )

    async def _delete_secret(self, name: str) -> None:
        try:
            await self._kube.delete_secret(name)
        except KubeError:
            log.exception("cannot delete secret %s", name)

    async def _tell(
        self, run: WorkItemDispatch, item: WorkItem | None, session: SessionRow | None
    ) -> None:
        """The owner's phone, like a finished note. Lab's notifier marks itself [lab]."""
        if item is None:
            return
        titles = {
            "pr_opened": "Jarvis: draft PR ready",
            "no_changes": "Jarvis: run made no changes",
            "rate_limited": "Jarvis: run hit the Claude usage limit",
            "timed_out": "Jarvis: run timed out",
        }
        title = titles.get(run.state, "Jarvis: run failed")
        detail = run.summary or run.error or ""
        message = f"**{item.title}** ({run.repo})" + (f"\n\n{detail.strip()}" if detail else "")
        url = run.pr_url or (session_url(self._cfg, str(session.id)) if session else None) or None
        try:
            await self._notifier.notify(title, message, 5 if run.state == "pr_opened" else 7, url)
        except Exception:
            log.exception("dispatch notification failed")
