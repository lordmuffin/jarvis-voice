"""The same calls as ``github.GitHubClient``, against a Forgejo (Gitea) API.

Forgejo's REST API (``/api/v1``) is close to GitHub's but not identical: a branch is made with
``POST /branches`` (no raw refs), a new file is ``POST contents`` while an update is ``PUT``,
pull requests cannot be filtered by head, and a merge is ``POST /pulls/{n}/merge`` with a
``Do`` field. Errors raise ``GitHubError`` so ``PublishService`` handles both backends alike."""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from jarvis_live.publish.github import (
    TIMEOUT_S,
    GitHubClient,
    GitHubError,
    PullRequest,
    _message,
)

log = logging.getLogger(__name__)


class ForgejoClient(GitHubClient):
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        repo: str,
        token_file: Path,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        super().__init__(
            client,
            api=f"{url.rstrip('/')}/api/v1",
            repo=repo,
            token_file=token_file,
            timeout_s=timeout_s,
        )

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise GitHubError(0, f"cannot read the Forgejo token: {e}") from e
        return {"Authorization": f"token {token}", "Accept": "application/json"}

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        ok: tuple[int, ...] = (200, 201),
    ) -> httpx.Response:
        try:
            r = await self._client.request(
                method,
                f"{self._api}/repos/{self.repo}{path}",
                json=json,
                params=params,
                headers=self._headers(),
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise GitHubError(0, f"Forgejo unreachable: {e!r}") from e
        if r.status_code == 429 and (ra := r.headers.get("retry-after")):
            log.warning("Forgejo rate limit on %s %s", method, path)
            await asyncio.sleep(min(float(ra) if ra.isdigit() else 5.0, 60.0))
            return await self._req(method, path, json=json, params=params, ok=ok)
        if r.status_code not in ok:
            raise GitHubError(r.status_code, f"Forgejo: {_message(r)}")
        return r

    async def branch_head(self, branch: str) -> str:
        r = await self._req("GET", f"/branches/{self._ref(branch)}")
        return str(r.json()["commit"]["id"])

    async def ensure_branch(self, branch: str, base_sha: str) -> None:
        """Create ``branch`` at ``base_sha``; a leftover branch of that name (ours) is replaced."""
        body = {"new_branch_name": branch, "old_ref_name": base_sha}
        try:
            await self._req("POST", "/branches", json=body)
        except GitHubError as e:
            if e.status not in (409, 422):
                raise
            await self._req("DELETE", f"/branches/{self._ref(branch)}", ok=(204,))
            await self._req("POST", "/branches", json=body)

    async def put_file(
        self,
        path: str,
        content: str,
        *,
        branch: str,
        message: str,
        sha: str | None,
        committer: dict[str, str],
    ) -> str:
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
            "committer": committer,
        }
        if sha is not None:
            body["sha"] = sha
        r = await self._req(
            "POST" if sha is None else "PUT", f"/contents/{quote(path, safe='/')}", json=body
        )
        return str(r.json()["commit"]["sha"])

    async def pr_files(self, number: int) -> list[str]:
        r = await self._req("GET", f"/pulls/{number}/files", params={"limit": "100"})
        return [str(f["filename"]) for f in r.json()]

    async def open_pr(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        try:
            r = await self._req(
                "POST", "/pulls", json={"head": head, "base": base, "title": title, "body": body}
            )
        except GitHubError as e:
            if e.status not in (409, 422):
                raise
            existing = await self.find_pr(head)
            if existing is None:
                raise
            return existing
        return PullRequest.of(r.json())

    async def find_pr(self, head: str) -> PullRequest | None:
        # Forgejo cannot filter by head: list the open PRs and match the branch name.
        r = await self._req("GET", "/pulls", params={"state": "open", "limit": "50"})
        for pr in r.json():
            if pr.get("head", {}).get("ref") == head:
                return PullRequest.of(pr)
        return None

    async def merge(self, number: int, *, sha: str, method: str, title: str) -> str:
        await self._req(
            "POST",
            f"/pulls/{number}/merge",
            json={"Do": method, "head_commit_id": sha, "MergeTitleField": title},
            ok=(200, 204),
        )
        return str((await self.get_pr(number)).head_sha)

    async def delete_branch(self, branch: str) -> None:
        try:
            await self._req("DELETE", f"/branches/{self._ref(branch)}", ok=(204,))
        except GitHubError as e:
            if e.status not in (404, 422):
                raise
