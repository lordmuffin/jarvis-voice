#!/usr/bin/env python3
"""jarvis-dispatch runner: the three steps of one dispatch Job (see jarvis_live/dispatch).

    runner.py prepare   fetch the repo's mirror, create this item's git worktree
    runner.py claude    run headless Claude Code in the worktree; leave a patch in $OUT
    runner.py push      apply the patch to a fresh clone, push the branch, open a draft PR

Standard library only. Each step runs in its own container with only its own credential:
prepare and push hold the GitHub token (a file), claude holds the Claude token (env).

The mirror ($MIRROR/<owner>__<name>.git) is shared across runs and written only by prepare,
under a lock. Each run gets a bare repo in its own $WORK whose objects borrow the mirror's
(alternates, read-only), and a worktree of it: `git worktree` per item, with nothing shared
that a run could change for the next one.

push never runs git in Claude's worktree and never reads its config: it takes only the patch
(data) and applies it to its own fresh clone with hooks disabled, so a hook or a
core.fsmonitor that Claude planted never executes with the token. It refuses changes to
.github/workflows (the token cannot push those either).

Claude runs unattended: it is told never to stop and ask, to commit as it goes (it has a git
author, but no GitHub credential), and to end with a `NEEDS INPUT:` section when only the owner
can unblock it. Those questions travel separately from its summary, and the owner's answer
starts a follow-up run on the same branch: prepare starts from the branch when it already
exists, and push reuses the branch's open PR instead of opening another.

push reports its outcome as JSON on $TERMINATION_LOG (the pod's termination message, max
4 KiB), which the server reads through the Kubernetes API."""

import base64
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

MAX_PATCH = 20 * 1024 * 1024
MAX_REPORT = 4000
MAX_BODY = 60_000
FORBIDDEN = (".github/workflows/",)
AUTHOR = ("Claude (jarvis-dispatch)", "claude-dispatch@apj.dev")
LIMIT = re.compile(r"(usage|rate)[ -]limit", re.IGNORECASE)
# The marker Claude ends with when it needs the owner, alone on a line, in any markdown dress.
ASK = re.compile(r"^[ \t#>*_]*NEEDS INPUT[ \t]*:?[ \t*_]*", re.IGNORECASE | re.MULTILINE)
MAX_QUESTIONS = 1500
UNATTENDED = """\
You are running unattended, as a Jarvis dispatch run in a sandboxed container. Nobody can \
answer while you work, so never stop to ask, wait for confirmation, or end early with a \
question you could answer yourself.

- When something is ambiguous, pick the most reasonable reading that fits the repository's \
own conventions, note the assumption, and keep going.
- Commit on the current branch as you go, with Conventional Commits messages; git already has \
an author. Do not push and do not open a pull request (there is no GitHub credential here): \
when you finish, the runner pushes this branch and opens or updates its draft PR.
- If the branch already has commits from an earlier run, build on them.
- Your final message becomes the PR description and the owner's notification: say what you \
changed, how you verified it, and the assumptions you made.
- Only if you truly cannot finish (a decision only the owner can make, access you lack, a \
check you cannot fix): do everything you can, commit it, then end your final message with a \
line that says only `NEEDS INPUT:` followed by numbered, answerable questions. The owner's \
answers start a follow-up run on this branch.
"""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit(f"{name} is not set")
    return value


def repo_url() -> str:
    return f"{env('GIT_BASE', 'https://github.com').rstrip('/')}/{env('REPO')}.git"


def token() -> str:
    return Path(env("TOKEN_FILE", "/secrets/github/token")).read_text().strip()


def auth_env(url: str) -> dict[str, str]:
    """The token as an HTTP header for this git process only: never in a URL or a config."""
    if not url.startswith("https://"):
        return {}
    basic = base64.b64encode(f"x-access-token:{token()}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def identity() -> dict[str, str]:
    """The author of every commit a run makes, Claude's own included."""
    return {
        "GIT_AUTHOR_NAME": AUTHOR[0],
        "GIT_AUTHOR_EMAIL": AUTHOR[1],
        "GIT_COMMITTER_NAME": AUTHOR[0],
        "GIT_COMMITTER_EMAIL": AUTHOR[1],
    }


def git(*args: str, cwd: Path | None = None, extra: dict[str, str] | None = None) -> str:
    """git with hooks and fsmonitor off, and no system or global config."""
    full = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **identity(),
        **(extra or {}),
    }
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    cmd += ["-c", "safe.directory=*", *args]
    out = subprocess.run(cmd, cwd=cwd, env=full, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"git {args[0]} failed: {out.stderr.strip()[-800:]}")
    return out.stdout.strip()


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    with open(path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# --- prepare ------------------------------------------------------------------------------


def prepare() -> None:
    repo, url = env("REPO"), repo_url()
    mirrors, work = Path(env("MIRROR", "/mirror")), Path(env("WORK", "/work"))
    key = repo.replace("/", "__")
    mirror = mirrors / f"{key}.git"
    with locked(mirrors / f"{key}.lock"):
        if (mirror / "HEAD").exists():
            git("remote", "set-url", "origin", url, cwd=mirror)
            git("fetch", "--prune", "--quiet", "origin", cwd=mirror, extra=auth_env(url))
        else:
            git("clone", "--mirror", "--quiet", url, str(mirror), extra=auth_env(url))
        default = git("symbolic-ref", "--short", "HEAD", cwd=mirror)
    local, branch = work / "repo.git", env("BRANCH")
    git("clone", "--bare", "--shared", "--quiet", str(mirror), str(local))
    try:  # a follow-up run: the branch an earlier run pushed
        base = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=local)
        git("worktree", "add", "--quiet", str(work / "tree"), branch, cwd=local)
        start = branch
    except RuntimeError:
        base = git("rev-parse", default, cwd=local)
        git("worktree", "add", "--quiet", "-b", branch, str(work / "tree"), base, cwd=local)
        start = default
    (work / "base").write_text(base + "\n")
    (work / "default").write_text(default + "\n")
    print(f"worktree {work / 'tree'} on {branch} from {start} at {base[:12]}")


# --- claude -------------------------------------------------------------------------------


def run_claude(prompt: str, cwd: Path) -> dict[str, Any]:
    """Headless Claude Code; the stream-json transcript goes to stdout (the pod log)."""
    cmd = [
        env("CLAUDE_BIN", "claude"),
        "-p",
        prompt,
        "--model",
        env("MODEL"),
        "--output-format",
        "stream-json",
        "--verbose",
        "--append-system-prompt",
        UNATTENDED,
        # The pod is the sandbox: no cluster or LAN access, no GitHub credential in this step.
        "--dangerously-skip-permissions",
    ]
    result: dict[str, Any] = {}
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, text=True, env={**os.environ, **identity()}
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    code = proc.wait()
    return {**result, "exit_code": code}


def split_questions(text: str) -> tuple[str, str]:
    """Claude's final message as (summary, questions): what follows its last NEEDS INPUT."""
    marks = list(ASK.finditer(text))
    if not marks:
        return text, ""
    m = marks[-1]
    return text[: m.start()].strip(), text[m.end() :].strip()


def claude() -> None:
    work, out = Path(env("WORK", "/work")), Path(env("OUT", "/out"))
    tree = work / "tree"
    base = (work / "base").read_text().strip()
    prompt = (Path(env("TASK", "/task")) / "prompt.md").read_text()
    try:
        r = run_claude(prompt, tree)
    except OSError as e:
        r = {"is_error": True, "result": f"could not start Claude Code: {e}", "exit_code": -1}
    text = str(r.get("result") or "").strip()
    failed = bool(r.get("is_error")) or r.get("exit_code") != 0 or not r
    status = ("rate_limited" if LIMIT.search(text) else "error") if failed else "ok"
    git("add", "-A", cwd=tree)
    (out / "changes.patch").write_text(git("diff", "--cached", "--binary", base, cwd=tree) + "\n")
    (out / "commits.txt").write_text(
        git("log", "--reverse", "--format=%B", f"{base}..HEAD", cwd=tree)
    )
    summary, questions = split_questions(text)
    summary = summary[:MAX_REPORT] or ("" if questions or not failed else "Claude Code failed")
    result = {
        "status": status,
        "summary": summary,
        "questions": questions[:MAX_QUESTIONS],
        "turns": r.get("num_turns"),
        "base": base,
        "default": (work / "default").read_text().strip(),
    }
    (out / "result.json").write_text(json.dumps(result))
    print(f"claude: {status}")


# --- push ---------------------------------------------------------------------------------


def report(**fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if v is not None}
    data = json.dumps(fields)
    for key in ("summary", "questions"):
        while len(data.encode()) > 4096 and len(fields.get(key) or "") > 1:
            fields[key] = fields[key][: len(fields[key]) // 2] + "…"
            data = json.dumps(fields)
    Path(env("TERMINATION_LOG", "/dev/termination-log")).write_text(data)
    print(data)


def touched(patch: str) -> set[str]:
    paths: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            for part in line[len("diff --git ") :].split(" "):
                if part.startswith(("a/", "b/")):
                    paths.add(part[2:])
        elif line.startswith(("rename to ", "rename from ", "copy to ", "copy from ")):
            paths.add(line.split(" ", 2)[2])
    return paths


def github(path: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        f"{env('GITHUB_API', 'https://api.github.com').rstrip('/')}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        what = "opening the PR" if body is not None else "looking up the PR"
        raise RuntimeError(f"{what} failed: {e.code} {e.read()[:300]!r}") from e


def open_pr(repo: str, branch: str, base: str, title: str, body: str) -> dict[str, Any]:
    """The branch's open PR (a follow-up run), else a new draft one."""
    query = urllib.parse.urlencode({"head": f"{repo.split('/')[0]}:{branch}", "state": "open"})
    found = github(f"/repos/{repo}/pulls?{query}")
    if isinstance(found, list) and found and isinstance(found[0], dict):
        return found[0]
    pr = {"title": title, "head": branch, "base": base, "body": body, "draft": True}
    data: dict[str, Any] = github(f"/repos/{repo}/pulls", pr)
    return data


def pr_body(
    summary: str, meta: dict[str, Any], prompt: str, commits: str, questions: str = ""
) -> str:
    body = f"{summary or 'Claude Code made these changes.'}\n\n"
    if questions:
        body += f"## Needs input\n\n{questions}\n\n"
    body += (
        "---\n\n"
        f"Dispatched from a Jarvis Live work item: {meta.get('item_url') or '(no link)'} "
        f"(dispatch `{meta.get('dispatch_id')}`). Headless Claude Code in its own worktree.\n\n"
    )
    if commits.strip():
        body += (
            f"<details><summary>Commits</summary>\n\n```\n{commits.strip()}\n```\n</details>\n\n"
        )
    body += (
        f"<details><summary>Prompt</summary>\n\n````markdown\n{prompt.strip()}\n````\n</details>\n"
    )
    return body[:MAX_BODY]


def push() -> None:
    out, task = Path(env("OUT", "/out")), Path(env("TASK", "/task"))
    repo, branch = env("REPO"), env("BRANCH")
    try:
        result = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        report(outcome="failed", error="the claude step left no result", branch=branch)
        return
    summary = result.get("summary") or ""
    questions = result.get("questions") or ""
    patch = (out / "changes.patch").read_text()
    if not patch.strip():
        if questions:
            report(
                outcome="needs_input", summary=summary or None, questions=questions, branch=branch
            )
        elif result["status"] == "ok":
            report(outcome="no_changes", summary=summary, branch=branch)
        else:
            outcome = "rate_limited" if result["status"] == "rate_limited" else "failed"
            report(outcome=outcome, error=summary or "Claude Code failed", branch=branch)
        return
    try:
        if bad := sorted(p for p in touched(patch) if p.startswith(FORBIDDEN)):
            files = ", ".join(bad)
            raise RuntimeError(f"the change touches .github/workflows ({files}): runs may not")
        if len(patch.encode()) > MAX_PATCH:
            raise RuntimeError("the change is larger than 20 MiB")
        meta = json.loads((task / "meta.json").read_text())
        prompt = (task / "prompt.md").read_text()
        commits = (out / "commits.txt").read_text()
        url = repo_url()
        clone = Path(env("SCRATCH", "/scratch")) / "repo"
        mirror = Path(env("MIRROR", "/mirror")) / f"{repo.replace('/', '__')}.git"
        reference = ["--reference-if-able", str(mirror)] if mirror.exists() else []
        git("clone", "--quiet", "--no-checkout", *reference, url, str(clone), extra=auth_env(url))
        git("checkout", "--quiet", "-b", branch, result["base"], cwd=clone)
        patch_file = Path(env("SCRATCH", "/scratch")) / "changes.patch"
        patch_file.write_text(patch)
        git("apply", "--index", "--binary", "--whitespace=nowarn", str(patch_file), cwd=clone)
        message = commits.strip() or f"chore: {meta.get('title', 'work item')}"
        git("commit", "--quiet", "--no-verify", "-m", message, cwd=clone)
        git(
            "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", cwd=clone, extra=auth_env(url)
        )
        pr = open_pr(
            repo,
            branch,
            result["default"],
            message.splitlines()[0][:200],
            pr_body(summary, meta, prompt, commits, questions),
        )
    except Exception as e:  # reported, not raised: the outcome travels in the report
        report(outcome="failed", error=str(e)[:1500], branch=branch, summary=summary[:1500] or None)
        return
    report(
        outcome="needs_input" if questions else "pr_opened",
        pr_url=pr.get("html_url"),
        pr_number=pr.get("number"),
        branch=branch,
        summary=summary[:1500],
        questions=questions or None,
    )


def main(argv: list[str]) -> int:
    steps = {"prepare": prepare, "claude": claude, "push": push}
    if len(argv) != 2 or argv[1] not in steps:
        print(__doc__)
        return 2
    steps[argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
