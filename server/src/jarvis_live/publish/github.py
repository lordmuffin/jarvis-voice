"""The handful of GitHub REST calls publishing needs: branch, file, pull request, merge.

No git binary and no clone: the image's root fs is read-only, and one note per branch is a
single ``PUT contents`` commit. The token (a fine-grained PAT with Contents and Pull requests
read+write) is read from its file on every call, so a rotated secret is picked up."""

import asyncio
import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

TIMEOUT_S = 30.0
MAX_RETRY_AFTER_S = 60.0


class GitHubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub {status}: {message}" if status else message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str  # the PR page
    state: str  # "open" | "closed"
    merged: bool
    head_sha: str
    mergeable: bool | None  # None while GitHub is still computing it

    @classmethod
    def of(cls, data: dict[str, Any]) -> "PullRequest":
        return cls(
            number=int(data["number"]),
            url=str(data["html_url"]),
            state=str(data["state"]),
            merged=bool(data.get("merged") or data.get("merged_at")),
            head_sha=str(data["head"]["sha"]),
            mergeable=data.get("mergeable"),
        )


def _message(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:500] or r.reason_phrase
    if isinstance(body, dict):
        msg = str(body.get("message") or "")
        errors = body.get("errors")
        if isinstance(errors, list):
            details = [str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors]
            msg = "; ".join([msg, *details]) if msg else "; ".join(details)
        return msg or r.reason_phrase
    return r.text[:500]


class GitHubClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api: str,
        repo: str,
        token_file: Path,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._client = client
        self._api = api.rstrip("/")
        self.repo = repo
        self.owner = repo.split("/", 1)[0]
        self._token_file = token_file
        self._timeout = timeout_s

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token_file.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise GitHubError(0, f"cannot read the GitHub token: {e}") from e
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _req(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        ok: tuple[int, ...] = (200, 201),
    ) -> httpx.Response:
        url = f"{self._api}/repos/{self.repo}{path}"
        for attempt in (1, 2):
            try:
                r = await self._client.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    headers=self._headers(),
                    timeout=self._timeout,
                )
            except httpx.HTTPError as e:
                raise GitHubError(0, f"GitHub unreachable: {e!r}") from e
            retry_after = r.headers.get("retry-after")
            if r.status_code in (403, 429) and retry_after and attempt == 1:
                # Secondary rate limit: wait as told (bounded), then try once more.
                try:
                    wait = min(float(retry_after), MAX_RETRY_AFTER_S)
                except ValueError:
                    wait = MAX_RETRY_AFTER_S
                log.warning("GitHub rate limit on %s %s; retrying in %.0fs", method, path, wait)
                await asyncio.sleep(wait)
                continue
            if r.status_code not in ok:
                raise GitHubError(r.status_code, _message(r))
            return r
        raise AssertionError("unreachable")

    @staticmethod
    def _ref(branch: str) -> str:
        return quote(branch, safe="/")

    async def branch_head(self, branch: str) -> str:
        r = await self._req("GET", f"/git/ref/heads/{self._ref(branch)}")
        return str(r.json()["object"]["sha"])

    async def ensure_branch(self, branch: str, base_sha: str) -> None:
        """Create ``branch`` at ``base_sha``. A leftover branch of the same name (ours: the
        prefix is reserved for this app) is reset to it."""
        try:
            await self._req(
                "POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha}
            )
        except GitHubError as e:
            if e.status != 422:
                raise
            await self._req(
                "PATCH",
                f"/git/refs/heads/{self._ref(branch)}",
                json={"sha": base_sha, "force": True},
            )

    async def file_sha(self, path: str, ref: str) -> str | None:
        """Blob sha of ``path`` on ``ref``; None if there is no such file."""
        try:
            r = await self._req("GET", f"/contents/{quote(path, safe='/')}", params={"ref": ref})
        except GitHubError as e:
            if e.status == 404:
                return None
            raise
        body = r.json()
        if not isinstance(body, dict) or body.get("type") != "file":
            raise GitHubError(409, f"{path} exists in the repository and is not a file")
        return str(body["sha"])

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
        """Commit ``content`` to ``path`` on ``branch``; returns the commit sha."""
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
            "committer": committer,
        }
        if sha is not None:
            body["sha"] = sha
        r = await self._req("PUT", f"/contents/{quote(path, safe='/')}", json=body)
        return str(r.json()["commit"]["sha"])

    async def open_pr(self, *, head: str, base: str, title: str, body: str) -> PullRequest:
        """Open a PR, or return the one already open for ``head``."""
        try:
            r = await self._req(
                "POST",
                "/pulls",
                json={"head": head, "base": base, "title": title, "body": body},
            )
        except GitHubError as e:
            if e.status != 422:
                raise
            existing = await self.find_pr(head)
            if existing is None:
                raise
            return existing
        return PullRequest.of(r.json())

    async def find_pr(self, head: str) -> PullRequest | None:
        r = await self._req(
            "GET", "/pulls", params={"head": f"{self.owner}:{head}", "state": "open"}
        )
        pulls = r.json()
        return PullRequest.of(pulls[0]) if pulls else None

    async def get_pr(self, number: int) -> PullRequest:
        r = await self._req("GET", f"/pulls/{number}")
        return PullRequest.of(r.json())

    async def merge(self, number: int, *, sha: str, method: str, title: str) -> str:
        """Merge only if the PR head is still ``sha`` (what the user saw); returns the merge
        commit sha."""
        r = await self._req(
            "PUT",
            f"/pulls/{number}/merge",
            json={"sha": sha, "merge_method": method, "commit_title": title},
        )
        return str(r.json().get("sha", ""))

    async def close_pr(self, number: int) -> PullRequest:
        r = await self._req("PATCH", f"/pulls/{number}", json={"state": "closed"})
        return PullRequest.of(r.json())

    async def delete_branch(self, branch: str) -> None:
        """Best effort: the repo may delete merged branches itself."""
        try:
            await self._req("DELETE", f"/git/refs/heads/{self._ref(branch)}", ok=(204,))
        except GitHubError as e:
            if e.status not in (404, 422):
                raise
