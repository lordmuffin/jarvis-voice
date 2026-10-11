"""GitHubClient against canned responses (httpx.MockTransport)."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from jarvis_live.publish.github import GitHubClient, GitHubError

Handler = Callable[[httpx.Request], httpx.Response]

PR = {
    "number": 7,
    "html_url": "https://github.com/o/r/pull/7",
    "state": "open",
    "merged": False,
    "head": {"sha": "abc"},
    "mergeable": None,
}


def make(tmp_path: Path, handler: Handler) -> GitHubClient:
    token = tmp_path / "token"
    token.write_text("tok\n")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubClient(client, api="https://api.test/", repo="o/r", token_file=token)


async def test_paths_are_url_encoded_and_the_token_is_sent(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(201, json={"commit": {"sha": "c1"}})

    gh = make(tmp_path, handler)
    path = "00 Inbox/Live Sessions/2026 - Sync (2).md"
    assert await gh.file_sha(path, "jarvis-live/b") is None
    sha = await gh.put_file(
        path, "héllo", branch="jarvis-live/b", message="m", sha=None, committer={"name": "n"}
    )
    assert sha == "c1"
    get, put = seen
    assert get.url.raw_path.decode() == (
        "/repos/o/r/contents/00%20Inbox/Live%20Sessions/2026%20-%20Sync%20%282%29.md"
        "?ref=jarvis-live%2Fb"
    )
    assert put.headers["authorization"] == "Bearer tok"
    body = json.loads(put.content)
    assert body["branch"] == "jarvis-live/b" and "sha" not in body
    assert body["content"] == "aMOpbGxv"  # base64 of utf-8 "héllo"


async def test_an_existing_branch_is_reset_and_an_existing_pr_reused(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "POST":
            return httpx.Response(422, json={"message": "Validation Failed"})
        if req.method == "PATCH":
            return httpx.Response(200, json={})
        assert req.url.params["head"] == "o:jarvis-live/b"
        return httpx.Response(200, json=[PR])

    gh = make(tmp_path, handler)
    await gh.ensure_branch("jarvis-live/b", "base")
    pr = await gh.open_pr(head="jarvis-live/b", base="main", title="t", body="b")
    assert pr.number == 7 and pr.mergeable is None and not pr.merged
    assert calls == [
        ("POST", "/repos/o/r/git/refs"),
        ("PATCH", "/repos/o/r/git/refs/heads/jarvis-live/b"),
        ("POST", "/repos/o/r/pulls"),
        ("GET", "/repos/o/r/pulls"),
    ]


async def test_errors_carry_githubs_message(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "Resource not accessible", "errors": [{"message": "needs PRs"}]},
        )

    gh = make(tmp_path, handler)
    with pytest.raises(GitHubError) as e:
        await gh.get_pr(1)
    assert e.value.status == 403
    assert "Resource not accessible" in str(e.value) and "needs PRs" in str(e.value)


async def test_branch_deletion_tolerates_a_branch_already_gone(tmp_path: Path) -> None:
    gh = make(tmp_path, lambda _: httpx.Response(422, json={"message": "Reference does not exist"}))
    await gh.delete_branch("jarvis-live/b")


async def test_a_missing_token_is_a_clear_error(tmp_path: Path) -> None:
    gh = make(tmp_path, lambda _: httpx.Response(200, json=PR))
    (tmp_path / "token").unlink()
    with pytest.raises(GitHubError, match="token"):
        await gh.get_pr(7)
