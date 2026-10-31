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
from urllib.parse import parse_qs, urlsplit

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
    queries: list[str] = []
    existing: list[dict[str, Any]] = []  # open PRs a lookup by head finds

    def do_GET(self) -> None:  # noqa: N802
        PRs.queries.append(self.path)
        out = json.dumps(PRs.existing).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

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
    PRs.created, PRs.queries, PRs.existing = [], [], []
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
        # Like the pod: no global git config, so no identity unless the runner gives one.
        "GIT_CONFIG_GLOBAL": os.devnull,
        "HOME": str(tmp_path),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    dirs["remote"], dirs["seed"], dirs["root"] = remote, seed, tmp_path
    return dirs


def fake_claude(world: dict[str, Path], script: str, result: dict[str, Any]) -> None:
    """A `claude` that runs ``script`` in the worktree, then prints a stream-json result."""
    path = world["root"] / "claude"
    events = [{"type": "system", "subtype": "init"}, {"type": "result", **result}]
    lines = "\n".join(f"printf '%s\\n' '{json.dumps(e)}'" for e in events)
    args = world["root"] / "claude-args"
    record = f'for a in "$@"; do printf \'%s\\0\' "$a"; done > {args}'
    path.write_text(f"#!/bin/sh\nset -e\n{record}\n{script}\n{lines}\n")
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


def claude_args(world: dict[str, Path]) -> list[str]:
    return (world["root"] / "claude-args").read_text().split("\0")[:-1]


PLAIN_COMMIT = (
    "printf 'print(\"retry\")\\n' >> app.py\n"
    "git add -A && git -c user.useConfigOnly=true commit -qm 'feat(server): retry Gotify pushes'"
)


def test_claude_can_commit_without_any_git_config(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, PLAIN_COMMIT, OK)
    runner.claude()
    assert json.loads((world["out"] / "result.json").read_text())["status"] == "ok"
    assert (world["out"] / "commits.txt").read_text().startswith("feat(server): retry")
    author = git("log", "-1", "--format=%an <%ae>", cwd=world["work"] / "tree")
    assert author == "Claude (jarvis-dispatch) <claude-dispatch@apj.dev>"


def test_claude_is_told_it_runs_unattended(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, "true", OK)
    runner.claude()
    args = claude_args(world)
    assert args[args.index("-p") + 1] == "# Task: retry\n"
    system = args[args.index("--append-system-prompt") + 1]
    assert "never stop to ask" in system and "NEEDS INPUT:" in system
    assert "Do not push" in system


ASKS = {
    **OK,
    "result": (
        "Added the retry loop and its tests.\n\n"
        "**NEEDS INPUT:**\n1. Should retries back off past 60 s?\n2. Keep the old flag?"
    ),
}


def test_questions_claude_ends_with_are_split_from_its_summary(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, PLAIN_COMMIT, ASKS)
    runner.claude()
    result = json.loads((world["out"] / "result.json").read_text())
    assert result["summary"] == "Added the retry loop and its tests."
    assert result["questions"] == "1. Should retries back off past 60 s?\n2. Keep the old flag?"


@pytest.mark.parametrize(
    "text",
    [
        "Needs input validation was added to the form.",
        "Done.\n\nNEEDS INPUT handling stays as it was.",
        "Done.\n\nNEEDS INPUT: none",
    ],
)
def test_only_the_marker_with_real_questions_asks(text: str) -> None:
    assert load().split_questions(text) == (text.strip(), "")


@pytest.mark.parametrize(
    "text",
    ["Done.\n**NEEDS INPUT:**\n1. Q?", "Done.\n## NEEDS INPUT\n1. Q?", "Done.\nNEEDS INPUT: 1. Q?"],
)
def test_the_marker_in_any_markdown_dress_asks(text: str) -> None:
    assert load().split_questions(text) == ("Done.", "1. Q?")


def test_a_run_with_questions_pushes_its_work_and_asks(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, PLAIN_COMMIT, ASKS)
    runner.claude()
    runner.push()
    r = report(world)
    assert r["outcome"] == "needs_input" and r["pr_number"] == 41
    assert r["questions"].startswith("1. Should retries")
    assert r["summary"] == "Added the retry loop and its tests."
    (pr,) = PRs.created
    assert "## Needs input" in pr["body"] and "Keep the old flag?" in pr["body"]


def test_questions_without_changes_still_ask(world: dict[str, Path]) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, "true", {**OK, "result": "NEEDS INPUT: which repo owns the config?"})
    runner.claude()
    runner.push()
    r = report(world)
    assert r == {
        "outcome": "needs_input",
        "questions": "which repo owns the config?",
        "branch": BRANCH,
    }
    assert PRs.created == []


def test_a_follow_up_run_continues_the_branch_and_its_pr(
    world: dict[str, Path], tmp_path: Path
) -> None:
    runner = load()
    runner.prepare()
    fake_claude(world, PLAIN_COMMIT, ASKS)
    runner.claude()
    runner.push()
    first = git("rev-parse", BRANCH, cwd=world["remote"])
    PRs.existing = [
        {"html_url": "https://github.com/lordmuffin/jarvis-voice/pull/41", "number": 41}
    ]
    PRs.created = []

    for step in ("work", "out", "scratch"):
        fresh = tmp_path / f"{step}2"
        fresh.mkdir()
        os.environ[step.upper()] = str(fresh)
    runner.prepare()
    tree = tmp_path / "work2" / "tree"
    assert git("rev-parse", "HEAD", cwd=tree) == first  # starts from the earlier run's work
    fake_claude(
        world,
        "printf 'x\\n' > more.txt && git add -A && git commit -qm 'test: more'",
        {**OK, "result": "Answered: no back-off past 60 s."},
    )
    runner.claude()
    runner.push()
    r = report(world)
    assert r["outcome"] == "pr_opened" and r["pr_number"] == 41
    assert PRs.created == []  # the open PR is reused, not duplicated
    for q in PRs.queries:
        url = urlsplit(q)
        assert url.path == f"/repos/{REPO}/pulls"
        assert parse_qs(url.query) == {"head": [f"lordmuffin:{BRANCH}"], "state": ["open"]}
    assert len(PRs.queries) == 2
    assert git("rev-parse", f"{BRANCH}~1", cwd=world["remote"]) == first  # a fast-forward
    assert git("show", f"{BRANCH}:more.txt", cwd=world["remote"]) == "x"
