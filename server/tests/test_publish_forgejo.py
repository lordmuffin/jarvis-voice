"""ForgejoClient: the calls publishing makes, against a mock Forgejo API."""

import json
from pathlib import Path

import httpx
import pytest

from jarvis_live.publish.forgejo import ForgejoClient
from jarvis_live.publish.github import GitHubError

PR = {
    "number": 7,
    "html_url": "https://git.example/o/r/pulls/7",
    "state": "open",
    "merged": False,
    "mergeable": True,
    "head": {"sha": "abc", "ref": "jarvis-live/x"},
}


def make(tmp_path: Path, handler: httpx.MockTransport) -> ForgejoClient:
    tok = tmp_path / "tok"
    tok.write_text("secret\n")
    return ForgejoClient(
        httpx.AsyncClient(transport=handler), url="https://git.example/", repo="o/r", token_file=tok
    )


async def test_auth_header_and_branch_head(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"commit": {"id": "sha1"}})

    c = make(tmp_path, httpx.MockTransport(h))
    assert await c.branch_head("main") == "sha1"
    assert str(seen[0].url) == "https://git.example/api/v1/repos/o/r/branches/main"
    assert seen[0].headers["authorization"] == "token secret"


async def test_put_file_creates_with_post_and_updates_with_put(tmp_path: Path) -> None:
    methods: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        methods.append(req.method)
        return httpx.Response(201, json={"commit": {"sha": "c1"}})

    c = make(tmp_path, httpx.MockTransport(h))
    kw = {"branch": "b", "message": "m", "committer": {"name": "n", "email": "e"}}
    assert await c.put_file("a b.md", "x", sha=None, **kw) == "c1"
    assert await c.put_file("a b.md", "x", sha="old", **kw) == "c1"
    assert methods == ["POST", "PUT"]


async def test_ensure_branch_replaces_leftover(tmp_path: Path) -> None:
    calls: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        if req.method == "POST" and calls.count("POST") == 1:
            return httpx.Response(409, json={"message": "exists"})
        body = json.loads(req.content) if req.content else {}
        assert body in ({}, {"new_branch_name": "b", "old_ref_name": "base"})
        return httpx.Response(204 if req.method == "DELETE" else 201, json={})

    c = make(tmp_path, httpx.MockTransport(h))
    await c.ensure_branch("b", "base")
    assert calls == ["POST", "DELETE", "POST"]


async def test_find_pr_matches_head_branch(tmp_path: Path) -> None:
    other = {**PR, "number": 3, "head": {"sha": "z", "ref": "other"}}
    c = make(tmp_path, httpx.MockTransport(lambda r: httpx.Response(200, json=[other, PR])))
    pr = await c.find_pr("jarvis-live/x")
    assert pr is not None and pr.number == 7


async def test_error_is_a_publish_error(tmp_path: Path) -> None:
    c = make(
        tmp_path,
        httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "token is required"})),
    )
    with pytest.raises(GitHubError) as e:
        await c.get_pr(1)
    assert e.value.status == 401
