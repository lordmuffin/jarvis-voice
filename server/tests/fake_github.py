"""A fake GitHub REST API: just the refs, contents and pull request calls publishing makes,
over an in-memory repository. Test double only.

Commits are snapshots ``{path: content}``; a branch points at one. ``merge`` lays the head's
files over the base's. ``merge_on_github`` / ``close_on_github`` act as a person would on
github.com, behind the app's back.
"""

import base64
import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

TOKEN = "fake-github-token"  # noqa: S105


def _sha(*parts: str) -> str:
    return hashlib.sha1("\0".join(parts).encode()).hexdigest()  # noqa: S324


def blob_sha(content: str) -> str:
    return _sha("blob", content)


@dataclass
class Pull:
    number: int
    head: str
    base: str
    title: str
    body: str
    state: str = "open"
    merged: bool = False


@dataclass
class FakeGitHub:
    url: str = "http://github.test"
    repo: str = "lordmuffin/Notes"
    commits: dict[str, dict[str, str]] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    pulls: dict[int, Pull] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    # (method, path suffix) -> (status, message): the next matching call fails once.
    fail_next: dict[tuple[str, str], tuple[int, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._n = itertools.count(1)
        root = _sha("root")
        self.commits[root] = {"README.md": "# Notes\n"}
        self.branches["main"] = root

    # --- inspection, and acting "on github.com" ---------------------------------------------

    def files(self, branch: str) -> dict[str, str]:
        return self.commits[self.branches[branch]]

    def pull_for(self, head: str) -> Pull | None:
        """The newest PR from ``head``."""
        return next((p for p in reversed(self.pulls.values()) if p.head == head), None)

    def merge_on_github(self, number: int) -> None:
        self._merge(self.pulls[number])

    def close_on_github(self, number: int) -> None:
        self.pulls[number].state = "closed"

    def _merge(self, pr: Pull) -> None:
        files = {**self.files(pr.base), **self.files(pr.head)}
        sha = _sha("merge", str(pr.number), self.branches[pr.base])
        self.commits[sha] = files
        self.branches[pr.base] = sha
        pr.state, pr.merged = "closed", True

    def _pull_json(self, pr: Pull) -> dict[str, Any]:
        return {
            "number": pr.number,
            "html_url": f"https://github.com/{self.repo}/pull/{pr.number}",
            "state": pr.state,
            "merged": pr.merged,
            "merged_at": "2026-10-11T00:00:00Z" if pr.merged else None,
            "head": {"ref": pr.head, "sha": self.branches.get(pr.head, "")},
            "base": {"ref": pr.base},
            "title": pr.title,
            "mergeable": True,
        }

    # --- the API ----------------------------------------------------------------------------

    def app(self) -> FastAPI:
        app = FastAPI()
        prefix = f"/repos/{self.repo}"

        @app.middleware("http")
        async def auth(request: Request, call_next: Any) -> Response:
            if request.headers.get("authorization") != f"Bearer {TOKEN}":
                return JSONResponse({"message": "Bad credentials"}, status_code=401)
            path = request.url.path.removeprefix(prefix)
            self.calls.append((request.method, path))
            for (method, suffix), (status, message) in list(self.fail_next.items()):
                if method == request.method and path.endswith(suffix):
                    del self.fail_next[(method, suffix)]
                    return JSONResponse({"message": message}, status_code=status)
            response: Response = await call_next(request)
            return response

        def err(status: int, message: str) -> HTTPException:
            return HTTPException(status, message)

        @app.exception_handler(HTTPException)
        async def http_error(_: Request, e: HTTPException) -> JSONResponse:
            return JSONResponse({"message": e.detail}, status_code=e.status_code)

        @app.get(prefix + "/git/ref/heads/{branch:path}")
        async def get_ref(branch: str) -> dict[str, Any]:
            if branch not in self.branches:
                raise err(404, "Not Found")
            return {"ref": f"refs/heads/{branch}", "object": {"sha": self.branches[branch]}}

        @app.post(prefix + "/git/refs", status_code=201)
        async def create_ref(body: dict[str, str]) -> dict[str, Any]:
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.branches:
                raise err(422, "Reference already exists")
            if body["sha"] not in self.commits:
                raise err(422, "Object does not exist")
            self.branches[name] = body["sha"]
            return {"ref": body["ref"], "object": {"sha": body["sha"]}}

        @app.patch(prefix + "/git/refs/heads/{branch:path}")
        async def update_ref(branch: str, body: dict[str, Any]) -> dict[str, Any]:
            if branch not in self.branches:
                raise err(422, "Reference does not exist")
            self.branches[branch] = body["sha"]
            return {"ref": f"refs/heads/{branch}", "object": {"sha": body["sha"]}}

        @app.delete(prefix + "/git/refs/heads/{branch:path}", status_code=204)
        async def delete_ref(branch: str) -> Response:
            if self.branches.pop(branch, None) is None:
                raise err(422, "Reference does not exist")
            return Response(status_code=204)

        @app.get(prefix + "/contents/{path:path}")
        async def get_contents(path: str, ref: str = "main") -> dict[str, Any]:
            files = self.files(ref) if ref in self.branches else {}
            if path not in files:
                raise err(404, "Not Found")
            return {"type": "file", "path": path, "sha": blob_sha(files[path])}

        @app.put(prefix + "/contents/{path:path}")
        async def put_contents(path: str, body: dict[str, Any]) -> dict[str, Any]:
            branch = body["branch"]
            if branch not in self.branches:
                raise err(404, "Branch not found")
            files = dict(self.files(branch))
            if path in files:
                if "sha" not in body:
                    raise err(422, '"sha" wasn\'t supplied.')
                if body["sha"] != blob_sha(files[path]):
                    raise err(409, f"{path} does not match {body['sha']}")
            files[path] = base64.b64decode(body["content"]).decode("utf-8")
            sha = _sha("commit", self.branches[branch], path, files[path], body["message"])
            self.commits[sha] = files
            self.branches[branch] = sha
            return {"content": {"path": path, "sha": blob_sha(files[path])}, "commit": {"sha": sha}}

        @app.post(prefix + "/pulls", status_code=201)
        async def create_pull(body: dict[str, str]) -> dict[str, Any]:
            head, base = body["head"], body["base"]
            if any(p.head == head and p.state == "open" for p in self.pulls.values()):
                raise err(422, f"A pull request already exists for {head}.")
            if self.files(head) == self.files(base):
                raise err(422, f"No commits between {base} and {head}")
            pr = Pull(next(self._n), head, base, body["title"], body["body"])
            self.pulls[pr.number] = pr
            return self._pull_json(pr)

        @app.get(prefix + "/pulls")
        async def list_pulls(head: str = "", state: str = "open") -> list[dict[str, Any]]:
            branch = head.split(":", 1)[-1]
            return [
                self._pull_json(p)
                for p in self.pulls.values()
                if (not branch or p.head == branch) and (state == "all" or p.state == state)
            ]

        @app.get(prefix + "/pulls/{number}")
        async def get_pull(number: int) -> dict[str, Any]:
            if number not in self.pulls:
                raise err(404, "Not Found")
            return self._pull_json(self.pulls[number])

        @app.patch(prefix + "/pulls/{number}")
        async def update_pull(number: int, body: dict[str, str]) -> dict[str, Any]:
            pr = self.pulls[number]
            if body.get("state") == "closed":
                pr.state = "closed"
            return self._pull_json(pr)

        @app.put(prefix + "/pulls/{number}/merge")
        async def merge_pull(number: int, body: dict[str, str]) -> dict[str, Any]:
            pr = self.pulls[number]
            if pr.state != "open":
                raise err(405, "Pull Request is not mergeable")
            if body.get("sha") and body["sha"] != self.branches[pr.head]:
                raise err(409, "Head branch was modified. Review and try the merge again.")
            self._merge(pr)
            return {"sha": self.branches[pr.base], "merged": True, "message": "merged"}

        return app
