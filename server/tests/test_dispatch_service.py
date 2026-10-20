"""DispatchService against a real Postgres and a fake Kubernetes: queue, start, finish,
refresh, cancel, and every way a run can end."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID, WorkItemDispatch, WorkItemEvent
from jarvis_live.dispatch.githubapp import TokenError
from jarvis_live.dispatch.service import DispatchService
from jarvis_live.workitems.dispatch import Actor
from jarvis_live.workitems.service import WorkItemError, WorkItemService
from tests.fake_kube import FakeKube, FakeTokens
from tests.fakes import FakeNotifier
from tests.work_item_helpers import REPOS, SM, owner_with_session, seed_item

CFG = Settings(
    work_items_enabled=True,
    work_repos=REPOS,
    dispatch_enabled=True,
    git_sha="abc1234def",
    public_base_url="https://live.example",
)
OWNER = Actor(OWNER_USER_ID, "web")


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


class Rig:
    def __init__(self, sm: SM, **cfg: Any) -> None:
        self.sm = sm
        self.cfg = CFG.model_copy(update=cfg)
        self.kube = FakeKube()
        self.tokens = FakeTokens()
        self.notifier = FakeNotifier()
        self.clock = Clock()
        self.svc = DispatchService(
            self.cfg, sm, self.kube, self.tokens, self.notifier, now=self.clock
        )
        self.work = WorkItemService(self.cfg, sm, self.svc)

    async def approved(self, **kw: Any) -> uuid.UUID:
        sid, _ = await owner_with_session(self.sm, OWNER_USER_ID)
        item = await seed_item(self.sm, sid, OWNER_USER_ID, **kw)
        await self.work.approve(item, OWNER)
        return item

    async def runs(self, item: uuid.UUID) -> list[WorkItemDispatch]:
        async with self.sm() as db:
            rows = await db.execute(
                select(WorkItemDispatch)
                .where(WorkItemDispatch.work_item_id == item)
                .order_by(WorkItemDispatch.attempt)
            )
            return list(rows.scalars().all())

    async def events(self, item: uuid.UUID) -> list[str]:
        async with self.sm() as db:
            rows = await db.execute(
                select(WorkItemEvent.event)
                .where(WorkItemEvent.work_item_id == item)
                .order_by(WorkItemEvent.id)
            )
            return list(rows.scalars().all())


@pytest.fixture
async def rig(sm: SM) -> Rig:
    # The loop is deployment-wide (one concurrency limit for every run), so runs left by
    # other tests in the shared database would hold its slot.
    async with sm() as db:
        await db.execute(delete(WorkItemDispatch))
        await db.commit()
    return Rig(sm)


async def test_a_dispatch_is_queued_then_started_as_a_job(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    (run,) = await rig.runs(item)
    assert (run.state, run.attempt, run.requested_by) == ("queued", 1, OWNER_USER_ID)
    assert run.branch.startswith("claude/work-") and run.branch.endswith(
        "-w1-retry-failed-gotify-notifications"
    )

    await rig.svc.tick()

    (run,) = await rig.runs(item)
    assert run.state == "running" and run.job_name == f"jarvis-dispatch-{run.id.hex[:8]}"
    assert rig.tokens.minted == ["lordmuffin/jarvis-voice"]
    secret = rig.kube.secrets[run.job_name]["stringData"]
    assert secret["token"] == "ghs_1"
    assert secret["prompt.md"].startswith("You are working in the GitHub repository")
    job = rig.kube.jobs[run.job_name]
    containers = job["spec"]["template"]["spec"]["initContainers"]
    assert containers[0]["image"] == "ghcr.io/lordmuffin/jarvis-dispatch-runner:sha-abc1234"
    # The Secret is owned by the Job, so it goes when the Job does.
    name, patch = rig.kube.patches[0]
    assert name == run.job_name
    assert patch["metadata"]["ownerReferences"][0]["uid"] == job["metadata"]["uid"]
    assert (await rig.events(item))[-2:] == ["dispatch_requested", "dispatch_started"]


async def test_one_run_at_a_time(rig: Rig) -> None:
    first, second = await rig.approved(), await rig.approved()
    await rig.work.dispatch(first, OWNER)
    await rig.work.dispatch(second, OWNER)
    await rig.svc.tick()
    assert [r.state for r in await rig.runs(first)] == ["running"]
    assert [r.state for r in await rig.runs(second)] == ["queued"]

    (run,) = await rig.runs(first)
    rig.kube.report(run.job_name, outcome="no_changes", summary="Nothing to do.")
    await rig.svc.tick()
    assert [r.state for r in await rig.runs(second)] == ["running"]


async def test_a_pr_finishes_the_run_and_tells_the_owner(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    rig.kube.report(
        run.job_name,
        outcome="pr_opened",
        pr_url="https://github.com/lordmuffin/jarvis-voice/pull/140",
        pr_number=140,
        branch=run.branch,
        summary="Added retries with backoff.",
    )
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    assert run.state == "pr_opened" and run.pr_number == 140
    assert run.summary == "Added retries with backoff." and run.finished_at is not None
    assert (await rig.events(item))[-1] == "dispatch_finished"
    (sent,) = rig.notifier.sent
    assert "pull/140" in (sent.url or "") and "Retry failed Gotify" in sent.message


async def test_a_deadline_times_the_run_out(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    rig.kube.deadline(run.job_name)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    assert run.state == "timed_out" and run.error == "the run hit its deadline"


async def test_a_long_run_gets_a_fresh_token(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    rig.kube.patches.clear()
    rig.clock.t += timedelta(minutes=29)
    await rig.svc.tick()
    assert rig.kube.patches == []
    rig.clock.t += timedelta(minutes=2)
    await rig.svc.tick()
    assert rig.kube.patches == [(run.job_name, {"stringData": {"token": "ghs_2"}})]


async def test_an_item_changed_before_its_run_started_does_not_run(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await rig.work.unapprove(item, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    assert run.state == "failed" and "changed" in (run.error or "")
    assert rig.kube.jobs == {} and rig.tokens.minted == []


async def test_a_refused_token_fails_the_run_before_anything_is_created(rig: Rig) -> None:
    rig.tokens.error = TokenError("GitHub 422: not installed")
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    assert run.state == "failed" and "not installed" in (run.error or "")
    assert rig.kube.secrets == {} and rig.kube.jobs == {}


async def test_a_refused_job_cleans_up_its_secret(rig: Rig) -> None:
    rig.kube.fail_job_create = True
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    assert run.state == "failed" and "forbidden" in (run.error or "")
    assert rig.kube.secrets == {} and len(rig.kube.deleted_secrets) == 1


async def test_only_one_active_run_per_item_and_a_retry_gets_its_own_branch(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    with pytest.raises(WorkItemError, match="already"):
        await rig.work.dispatch(item, OWNER)
    assert (await rig.events(item))[-1] == "dispatch_refused"
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    rig.kube.report(run.job_name, outcome="failed", error="tests failed")
    await rig.svc.tick()
    await rig.work.dispatch(item, OWNER)
    first, second = await rig.runs(item)
    assert (first.state, second.state, second.attempt) == ("failed", "queued", 2)
    assert second.branch == first.branch + "-2"


async def test_cancel_stops_a_running_or_queued_run(rig: Rig) -> None:
    running, queued = await rig.approved(), await rig.approved()
    await rig.work.dispatch(running, OWNER)
    await rig.work.dispatch(queued, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(running)
    await rig.svc.cancel(running, OWNER)
    await rig.svc.cancel(queued, OWNER)
    assert rig.kube.deleted_jobs == [run.job_name]
    assert [r.state for r in await rig.runs(running)] == ["cancelled"]
    assert [r.state for r in await rig.runs(queued)] == ["cancelled"]
    assert (await rig.events(running))[-1] == "dispatch_cancelled"
    with pytest.raises(WorkItemError, match="nothing to cancel"):
        await rig.svc.cancel(running, OWNER)


async def test_another_user_cannot_cancel(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    with pytest.raises(WorkItemError) as e:
        await rig.svc.cancel(item, Actor(uuid.uuid4(), "web"))
    assert e.value.status == 404


async def test_a_run_that_needs_input_asks_the_owner(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    rig.kube.report(
        run.job_name,
        outcome="needs_input",
        pr_url="https://github.com/lordmuffin/jarvis-voice/pull/140",
        pr_number=140,
        summary="Added the retry loop.",
        questions="1. Back off past 60 s?",
    )
    await rig.svc.tick()
    (run,) = await rig.runs(item)
    assert (run.state, run.questions, run.pr_number) == (
        "needs_input",
        "1. Back off past 60 s?",
        140,
    )
    (sent,) = rig.notifier.sent
    assert sent.title == "Jarvis: run needs your input"
    assert "Back off past 60 s?" in sent.message
    assert sent.url == "https://live.example/work-items"


async def finish(rig: Rig, item: uuid.UUID, **report: Any) -> WorkItemDispatch:
    await rig.svc.tick()
    run = (await rig.runs(item))[-1]
    rig.kube.report(run.job_name, **report)
    await rig.svc.tick()
    return (await rig.runs(item))[-1]


async def test_guidance_continues_the_last_run_on_its_branch(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    first = await finish(
        rig,
        item,
        outcome="needs_input",
        pr_url="https://github.com/lordmuffin/jarvis-voice/pull/140",
        pr_number=140,
        summary="Added the retry loop.",
        questions="1. Back off past 60 s?",
    )

    await rig.work.dispatch(item, OWNER, guidance="  No, cap it at 60 s.  ")

    _, second = await rig.runs(item)
    assert (second.state, second.attempt, second.branch) == ("queued", 2, first.branch)
    assert (second.continues, second.guidance) == (first.id, "No, cap it at 60 s.")
    async with rig.sm() as db:
        payload = (
            (
                await db.execute(
                    select(WorkItemEvent.payload)
                    .where(WorkItemEvent.work_item_id == item)
                    .where(WorkItemEvent.event == "dispatch_requested")
                    .order_by(WorkItemEvent.id.desc())
                )
            )
            .scalars()
            .first()
        )
    assert payload is not None and payload["guidance"] == "No, cap it at 60 s."

    await rig.svc.tick()
    (second,) = [r for r in await rig.runs(item) if r.attempt == 2]
    prompt = rig.kube.secrets[second.job_name]["stringData"]["prompt.md"]
    assert prompt.startswith("You are working in the GitHub repository")
    follow_up = prompt[prompt.index("## Follow-up run") :]
    assert f"`{first.branch}`" in follow_up and "draft PR #140" in follow_up
    assert "Added the retry loop." in follow_up and "1. Back off past 60 s?" in follow_up
    assert follow_up.rstrip().endswith("No, cap it at 60 s.")


async def test_guidance_needs_an_earlier_finished_run(rig: Rig) -> None:
    item = await rig.approved()
    with pytest.raises(WorkItemError, match="nothing to continue"):
        await rig.work.dispatch(item, OWNER, guidance="go on")
    await rig.work.dispatch(item, OWNER)
    with pytest.raises(WorkItemError, match="already"):
        await rig.work.dispatch(item, OWNER, guidance="go on")
    with pytest.raises(WorkItemError, match="guidance"):
        await rig.work.dispatch(item, OWNER, guidance="   ")
    assert [r.attempt for r in await rig.runs(item)] == [1]


async def test_guidance_still_needs_the_approved_prompt(rig: Rig) -> None:
    item = await rig.approved()
    await rig.work.dispatch(item, OWNER)
    await finish(rig, item, outcome="failed", error="tests failed")
    await rig.work.unapprove(item, OWNER)
    with pytest.raises(WorkItemError, match="not approved"):
        await rig.work.dispatch(item, OWNER, guidance="fix the tests")
