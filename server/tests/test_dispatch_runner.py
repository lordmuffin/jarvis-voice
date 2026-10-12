"""The runner's three steps against real git: local "GitHub" repos over file://, a fake claude
binary and a fake pull-request API. No network, no cluster."""

import importlib.util
import json
import os
import stat
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "dispatch-runner" / "runner.py"
REPO = "lordmuffin/jarvis-voice"
BRANCH = "claude/work-3f2a9c-w1-retry"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dispatch_runner", RUNNER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(*args: str, cwd: Path | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


class PRs(BaseHTTPRequestHandler):
    created: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        PRs.created.append({"path": self.path, "auth": self.headers.get("Authorization"), **body})
        out = json.dumps(
            {"html_url": "https://github.com/lordmuffin/jarvis-voice/pull/41", "number": 41}
        ).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def api() -> Iterator[str]:
    PRs.created = []
    server = HTTPServer(("127.0.0.1", 0), PRs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def world(tmp_path: Path, api: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A remote repo with main, plus the directories the pod mounts."""
    remote = tmp_path / "remote" / f"{REPO}.git"
    seed = tmp_path / "seed"
    git("init", "-q", "-b", "main", str(seed))
    (seed / "app.py").write_text("print('hi')\n")
    (seed / ".github" / "workflows").mkdir(parents=True)
    (seed / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    git("add", "-A", cwd=seed)
    git("commit", "-qm", "init", cwd=seed)
    remote.parent.mkdir(parents=True)
    git("clone", "-q", "--bare", str(seed), str(remote))
    dirs = {
        name: tmp_path / name for name in ("mirror", "work", "out", "task", "scratch", "secrets")
    }
    for d in dirs.values():
        d.mkdir()
    (dirs["secrets"] / "token").write_text("ghs_test\n")
    (dirs["task"] / "prompt.md").write_text("# Task: retry\n")
    meta = {
        "repo": REPO,
        "branch": BRANCH,
        "title": "Retry failed Gotify notifications",
        "item_url": "https://live.example/sessions/s",
        "dispatch_id": "d-1",
    }
    (dirs["task"] / "meta.json").write_text(json.dumps(meta))
    env = {
        "REPO": REPO,
        "BRANCH": BRANCH,
        "GIT_BASE": f"file://{tmp_path / 'remote'}",
        "GITHUB_API": api,
        "MIRROR": str(dirs["mirror"]),
        "WORK": str(dirs["work"]),
        "OUT": str(dirs["out"]),
        "TASK": str(dirs["task"]),
        "SCRATCH": str(dirs["scratch"]),
        "TOKEN_FILE": str(dirs["secrets"] / "token"),
        "MODEL": "claude-sonnet-5-5",
        "TERMINATION_LOG": str(tmp_path / "termination-log"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    dirs["remote"], dirs["seed"], dirs["root"] = remote, seed, tmp_path
    return dirs


def fake_claude(world: dict[str, Path], script: str, result: dict[str, Any]) -> None:
    """A `claude` that runs ``script`` in the worktree, then prints a stream-json result."""
    path = world["root"] / "claude"
    events = [{"type": "system", "subtype": "init"}, {"type": "result", **result}]
    lines = "\n".join(f"echo '{json.dumps(e)}'" for e in events)
    path.write_text(f"#!/bin/sh\nset -e\n{script}\n{lines}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    os.environ["CLAUDE_BIN"] = str(path)


def report(world: dict[str, Path]) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((world["root"] / "termination-log").read_text())
    return data


OK = {
    "subtype": "success",
    "is_error": False,
    "result": "Added retries with backoff.",
    "num_turns": 7,
}
EDIT = (
    "printf 'print(\"retry\")\\n' >> app.py\n"
    "git add -A && "
    "git -c user.name=c -c user.email=c@c commit -qm 'feat(server): retry Gotify pushes'"
)


def test_prepare_creates_a_worktree_on_the_branch_from_a_mirror(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    tree = world["work"] / "tree"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=tree) == BRANCH
    assert (tree / "app.py").exists()
    assert (tree / ".git").is_file()  # a linked worktree, not a clone
    assert "worktree" in git("worktree", "list", cwd=tree) or git("worktree", "list", cwd=tree)
    alternates = (world["work"] / "repo.git" / "objects" / "info" / "alternates").read_text()
    assert str(world["mirror"]) in alternates
    assert (world["work"] / "base").read_text().strip() == git(
        "rev-parse", "main", cwd=world["seed"]
    )
    assert (world["work"] / "default").read_text().strip() == "main"


def test_the_next_run_fetches_new_commits_into_the_mirror(
    world: dict[str, Path], tmp_path: Path
) -> None:
    runner = load()
    runner.prepare()
    (world["seed"] / "new.txt").write_text("new\n")
    git("add", "-A", cwd=world["seed"])
    git("commit", "-qm", "new", cwd=world["seed"])
    git("push", "-q", str(world["remote"]), "main", cwd=world["seed"])
    second = tmp_path / "work2"
    second.mkdir()
    os.environ["WORK"] = str(second)
    runner.prepare()
    assert (second / "tree" / "new.txt").exists()


def test_claude_runs_in_the_worktree_and_leaves_a_patch(
    world: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, EDIT, OK)
    runner.claude()
    result = json.loads((world["out"] / "result.json").read_text())
    assert result["status"] == "ok" and result["summary"] == "Added retries with backoff."
    assert result["default"] == "main" and len(result["base"]) == 40
    patch = (world["out"] / "changes.patch").read_text()
    assert '+print("retry")' in patch
    assert (
        (world["out"] / "commits.txt").read_text().startswith("feat(server): retry Gotify pushes")
    )
    assert '"type": "result"' in capsys.readouterr().out  # the transcript goes to the pod log


def test_a_usage_limit_is_reported_as_such(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(
        world,
        "true",
        {
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "Claude usage limit reached. Your limit will reset at 5pm.",
        },
    )
    runner.claude()
    assert json.loads((world["out"] / "result.json").read_text())["status"] == "rate_limited"


def test_push_opens_a_draft_pr_from_a_fresh_clone(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, EDIT, OK)
    runner.claude()
    runner.push()
    r = report(world)
    assert r == {
        "outcome": "pr_opened",
        "pr_url": "https://github.com/lordmuffin/jarvis-voice/pull/41",
        "pr_number": 41,
        "branch": BRANCH,
        "summary": "Added retries with backoff.",
    }
    pushed = git("show", f"{BRANCH}:app.py", cwd=world["remote"])
    assert 'print("retry")' in pushed
    assert (
        git("log", "-1", "--format=%s", BRANCH, cwd=world["remote"])
        == "feat(server): retry Gotify pushes"
    )
    (pr,) = PRs.created
    assert pr["path"] == f"/repos/{REPO}/pulls"
    assert pr["draft"] is True and pr["head"] == BRANCH and pr["base"] == "main"
    assert pr["title"] == "feat(server): retry Gotify pushes"
    assert pr["auth"] == "Bearer ghs_test"
    assert "Added retries with backoff." in pr["body"] and "# Task: retry" in pr["body"]


def test_push_refuses_a_change_to_ci_workflows(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, "echo 'on: pull_request' > .github/workflows/ci.yml", OK)
    runner.claude()
    runner.push()
    r = report(world)
    assert r["outcome"] == "failed" and ".github/workflows" in r["error"]
    assert PRs.created == []
    with pytest.raises(subprocess.CalledProcessError):
        git("rev-parse", "--verify", BRANCH, cwd=world["remote"])


def test_no_changes_means_no_pr(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, "true", {**OK, "result": "Already done."})
    runner.claude()
    runner.push()
    assert report(world) == {"outcome": "no_changes", "summary": "Already done.", "branch": BRANCH}
    assert PRs.created == []


def test_nothing_claude_plants_in_its_worktree_runs_in_push(world: dict[str, Path]) -> None:
    marker = world["root"] / "pwned"
    hostile = (
        f"git config core.fsmonitor 'touch {marker}'\n"
        "git config core.hooksPath hooks && mkdir -p hooks\n"
        f"printf '#!/bin/sh\\ntouch {marker}\\n' > hooks/pre-commit && chmod +x hooks/pre-commit\n"
        + EDIT
    )
    runner = load()
    runner.prepare()
    fake_claude(world, hostile, OK)
    marker.unlink(missing_ok=True)
    runner.claude()
    marker.unlink(missing_ok=True)  # the claude step is Claude's own sandbox; only push matters
    runner.push()
    assert report(world)["outcome"] == "pr_opened"
    assert not marker.exists()
