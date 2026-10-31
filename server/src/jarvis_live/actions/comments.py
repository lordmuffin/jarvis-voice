"""Executing approved ``git.pr_comment`` actions with the GitHub App.

Each comment mints a token for the one repository with only Pull requests write (and Metadata
read), the least GitHub accepts for a PR comment. Only repos whose pull requests live on GitHub
qualify: a repo mapped to Forgejo has only a mirror here, and Forgejo's token is read-only.
The comment carries ``<!-- jarvis-action:<id> -->``, so running the action again finds it
instead of posting twice."""

from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from jarvis_live.actions.service import ExecutionError
from jarvis_live.config import Settings
from jarvis_live.db.models import ActionProposal
from jarvis_live.dispatch.githubapp import TokenError

COMMENT_PERMISSIONS = {"pull_requests": "write", "metadata": "read"}
TIMEOUT_S = 30.0
PAGE = 100
MAX_PAGES = 10  # the marker is on one of the PR's last 1000 comments, or we say so


class CommentTokens(Protocol):
    async def mint(self, repo: str, permissions: dict[str, str] | None = None) -> str: ...


def commentable_repos(cfg: Settings) -> frozenset[str]:
    """Repos the App can reach (dispatch's allowlist) whose PRs are on GitHub."""
    return frozenset(cfg.dispatch_repo_allowlist) - frozenset(cfg.work_forgejo_repo_map)


def marker_of(proposal: ActionProposal) -> str:
    return f"<!-- jarvis-action:{proposal.id} -->"


class PrCommentExecutor:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient, tokens: CommentTokens
    ) -> None:
        self._api = settings.github_api.rstrip("/")
        self._client = client
        self._tokens = tokens

    async def execute(self, proposal: ActionProposal) -> dict[str, Any]:
        repo, number = proposal.payload["repo"], int(proposal.payload["pr"])
        headers = await self._headers(repo)
        r = await self._call("GET", f"/repos/{repo}/pulls/{number}", headers)
        if r.status_code == 404:
            raise ExecutionError(f"no pull request #{number} in {repo}")
        self._ok(r)
        if (found := await self._find(proposal, headers)) is not None:
            return {**found, "landed": "already_present"}
        body = (
            f"{proposal.payload['body'].strip()}\n\n"
            "<sub>Posted by Jarvis Live from a voice session; the owner approved this "
            f"comment.</sub>\n{marker_of(proposal)}"
        )
        r = await self._call(
            "POST", f"/repos/{repo}/issues/{number}/comments", headers, json={"body": body}
        )
        self._ok(r)
        c = r.json()
        return {
            "landed": "posted",
            "repo": repo,
            "pr": number,
            "comment_id": c["id"],
            "comment_url": c["html_url"],
        }

    async def reconcile(self, proposal: ActionProposal) -> dict[str, Any] | None:
        try:
            found = await self._find(proposal, await self._headers(proposal.payload["repo"]))
        except ExecutionError:
            return None
        return {**found, "landed": "already_present"} if found else None

    # --- GitHub -----------------------------------------------------------------------------

    async def _headers(self, repo: str) -> dict[str, str]:
        try:
            token = await self._tokens.mint(repo, permissions=dict(COMMENT_PERMISSIONS))
        except TokenError as e:
            raise ExecutionError(f"cannot get a GitHub token for {repo}: {e}") from e
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _find(self, p: ActionProposal, headers: Mapping[str, str]) -> dict[str, Any] | None:
        repo, number = p.payload["repo"], int(p.payload["pr"])
        for page in range(1, MAX_PAGES + 1):
            r = await self._call(
                "GET",
                f"/repos/{repo}/issues/{number}/comments",
                headers,
                params={"per_page": str(PAGE), "page": str(page)},
            )
            self._ok(r)
            comments = r.json()
            for c in comments:
                if marker_of(p) in (c.get("body") or ""):
                    return {
                        "repo": repo,
                        "pr": number,
                        "comment_id": c["id"],
                        "comment_url": c["html_url"],
                    }
            if len(comments) < PAGE:
                return None
        return None

    async def _call(
        self, method: str, path: str, headers: Mapping[str, str], **kw: Any
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method, self._api + path, headers=dict(headers), timeout=TIMEOUT_S, **kw
            )
        except httpx.HTTPError as e:
            raise ExecutionError(f"GitHub unreachable: {e!r}") from e

    @staticmethod
    def _ok(r: httpx.Response) -> None:
        if r.status_code >= 400:
            try:
                message = str(r.json().get("message") or r.text)
            except ValueError:
                message = r.text
            raise ExecutionError(f"GitHub {r.status_code}: {message[:300]}")
