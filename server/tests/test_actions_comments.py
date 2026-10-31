"""Posting an approved PR comment with the GitHub App: a token for one repo with only
Pull requests write, a marker so it posts once, and only on repos whose PRs live on GitHub."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from jarvis_live.actions.comments import PrCommentExecutor, commentable_repos
from jarvis_live.actions.service import ExecutionError
from jarvis_live.actions.state import payload_sha256
from jarvis_live.config import Settings
from jarvis_live.copilot.prompts import COPILOT_SYSTEM, FINAL_SYSTEM, copilot_user
from jarvis_live.db.models import OWNER_USER_ID, ActionProposal
from jarvis_live.dispatch.githubapp import TokenError

REPO = "lordmuffin/jarvis-voice"


class Tokens:
    def __init__(self, fail: str | None = None) -> None:
        self.minted: list[tuple[str, dict[str, str] | None]] = []
        self.fail = fail

    async def mint(self, repo: str, permissions: dict[str, str] | None = None) -> str:
        if self.fail:
            raise TokenError(self.fail)
        self.minted.append((repo, permissions))
        return "ghs_comment"


class GitHub:
    """Just the three calls the executor makes, over in-memory PRs and comments."""

    def __init__(self) -> None:
        self.prs = {214}
        self.comments: dict[int, list[dict[str, Any]]] = {214: []}
        self.requests: list[httpx.Request] = []
        self.fail: tuple[int, str] | None = None

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.headers.get("authorization") != "Bearer ghs_comment":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if self.fail and req.method == "POST":
            return httpx.Response(self.fail[0], json={"message": self.fail[1]})
        parts = req.url.path.strip("/").split("/")  # repos/o/r/(pulls|issues)/N[/comments]
        n = int(parts[4])
        if parts[3] == "pulls":
            return httpx.Response(200 if n in self.prs else 404, json={"number": n})
        assert parts[3] == "issues" and parts[5] == "comments"
        if req.method == "POST":
            cid = 900 + len(self.comments[n])
            c = {
                "id": cid,
                "body": json.loads(req.content)["body"],
                "html_url": f"https://github.com/{REPO}/pull/{n}#issuecomment-{cid}",
            }
            self.comments[n].append(c)
            return httpx.Response(201, json=c)
        page = int(req.url.params.get("page", "1"))
        per = int(req.url.params.get("per_page", "30"))
        return httpx.Response(200, json=self.comments[n][(page - 1) * per : page * per])

    @property
    def posts(self) -> int:
        return sum(r.method == "POST" for r in self.requests)


def make(gh: GitHub, tokens: Tokens | None = None) -> tuple[PrCommentExecutor, Tokens]:
    tokens = tokens or Tokens()
    client = httpx.AsyncClient(transport=httpx.MockTransport(gh.handler))
    return PrCommentExecutor(Settings(github_api="https://api.github.test"), client, tokens), tokens


def proposal(**payload: Any) -> ActionProposal:
    payload = payload or {"repo": REPO, "pr": 214, "body": "The migration needs a downgrade."}
    return ActionProposal(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=OWNER_USER_ID,
        ref="p1",
        kind="git.pr_comment",
        payload=payload,
        payload_sha256=payload_sha256("git.pr_comment", payload),
        evidence=[],
    )


async def test_posts_the_comment_with_a_narrow_token_and_a_marker() -> None:
    gh = GitHub()
    ex, tokens = make(gh)
    p = proposal()
    result = await ex.execute(p)
    assert tokens.minted == [(REPO, {"pull_requests": "write", "metadata": "read"})]
    [c] = gh.comments[214]
    assert c["body"].startswith("The migration needs a downgrade.")
    assert f"<!-- jarvis-action:{p.id} -->" in c["body"] and "approved" in c["body"]
    assert result["comment_url"] == c["html_url"] and result["landed"] == "posted"


async def test_running_it_again_posts_nothing() -> None:
    gh = GitHub()
    ex, _ = make(gh)
    p = proposal()
    await ex.execute(p)
    again = await ex.execute(p)
    assert again["landed"] == "already_present" and gh.posts == 1


async def test_the_marker_is_found_on_a_later_page() -> None:
    gh = GitHub()
    ex, _ = make(gh)
    p = proposal()
    await ex.execute(p)
    gh.comments[214] = [
        {"id": i, "body": "chatter", "html_url": "u"} for i in range(150)
    ] + gh.comments[214]
    assert (await ex.execute(p))["landed"] == "already_present" and gh.posts == 1


async def test_reconcile_only_looks() -> None:
    gh = GitHub()
    ex, _ = make(gh)
    p = proposal()
    assert await ex.reconcile(p) is None
    await ex.execute(p)
    before = gh.posts
    assert (await ex.reconcile(p)) is not None and gh.posts == before


@pytest.mark.parametrize(
    ("setup", "match"),
    [
        (lambda gh, t: gh.prs.clear(), "no pull request #214"),
        (lambda gh, t: setattr(gh, "fail", (403, "Resource not accessible")), "403"),
        (lambda gh, t: setattr(t, "fail", "installation not found"), "installation not found"),
    ],
)
async def test_failures_are_reported(setup: Callable[[GitHub, Tokens], None], match: str) -> None:
    gh, tokens = GitHub(), Tokens()
    setup(gh, tokens)
    ex, _ = make(gh, tokens)
    with pytest.raises(ExecutionError, match=match):
        await ex.execute(proposal())
    assert gh.comments[214] == []


# --- which repos --------------------------------------------------------------------------


def test_only_repos_whose_prs_live_on_github_are_commentable() -> None:
    cfg = Settings(
        work_repos="lordmuffin/jarvis-voice  Jarvis\nlordmuffin/np-home-homelab  homelab",
        work_forgejo_repos="lordmuffin/np-home-homelab  lordmuffin/np-home-homelab",
    )
    assert commentable_repos(cfg) == frozenset({REPO})
    sandbox = Settings(
        work_repos="lordmuffin/jarvis-voice  Jarvis\nlordmuffin/sandbox  s",
        dispatch_repos="lordmuffin/sandbox  s",
    )
    assert commentable_repos(sandbox) == frozenset({"lordmuffin/sandbox"})  # the App's reach


def test_the_copilot_is_offered_pr_comments_only_with_repos_to_name() -> None:
    assert "git.pr_comment" in COPILOT_SYSTEM and "git.pr_comment" not in FINAL_SYSTEM
    base: dict[str, Any] = {
        "today": "d",
        "title": "t",
        "state_json": "{}",
        "related": "",
        "transcript": "",
    }
    assert "REPOSITORIES" not in copilot_user(**base)
    assert f"REPOSITORIES YOU MAY COMMENT ON\n- {REPO}" in copilot_user(**base, repos=[REPO])


# --- wiring: every executor the deployment can support is registered together -------------


def test_the_app_registers_every_executor_its_credentials_allow(tmp_path: Any) -> None:
    from jarvis_live.actions.wiring import build_executors
    from jarvis_live.notify.gotify import NullNotifier

    client = httpx.AsyncClient()
    cfg = Settings(work_repos=f"{REPO}  Jarvis")
    notifier = NullNotifier()
    sm: Any = None  # the executors only keep it

    kinds, commentable = build_executors(cfg, sm, client, None, notifier, None)
    assert sorted(kinds) == ["reminder.schedule"]  # no vault credential, no GitHub App (lab)
    assert commentable == frozenset()

    gh: Any = object()
    kinds, _ = build_executors(cfg, sm, client, gh, notifier, None)
    assert sorted(kinds) == ["reminder.schedule", "task.create", "vault.append"]

    kinds, commentable = build_executors(cfg, sm, client, gh, notifier, Tokens())
    assert sorted(kinds) == ["git.pr_comment", "reminder.schedule", "task.create", "vault.append"]
    assert commentable == frozenset({REPO})
