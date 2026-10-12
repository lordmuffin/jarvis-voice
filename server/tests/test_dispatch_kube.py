"""The Kubernetes REST calls dispatch makes, and how a Job's state is read back."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis_live.dispatch.kube import KubeClient, KubeError, RunState, interpret

Requests = list[httpx.Request]


def client(tmp_path: Path, handler: Any) -> tuple[KubeClient, Requests]:
    seen: Requests = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)  # type: ignore[no-any-return]

    token = tmp_path / "token"
    token.write_text("sa-token\n")
    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return KubeClient(http, api="https://kube", token_file=token, namespace="jarvis-live-lab"), seen


async def test_creates_jobs_and_secrets_in_its_namespace_with_the_sa_token(tmp_path: Path) -> None:
    kube, seen = client(tmp_path, lambda r: httpx.Response(201, json={"metadata": {"uid": "u1"}}))
    job = await kube.create_job({"kind": "Job", "metadata": {"name": "j"}})
    await kube.create_secret({"kind": "Secret", "metadata": {"name": "j"}})
    assert job["metadata"]["uid"] == "u1"
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/apis/batch/v1/namespaces/jarvis-live-lab/jobs"),
        ("POST", "/api/v1/namespaces/jarvis-live-lab/secrets"),
    ]
    assert seen[0].headers["Authorization"] == "Bearer sa-token"
    assert json.loads(seen[0].content)["metadata"]["name"] == "j"


async def test_patches_secrets_as_merge_patches(tmp_path: Path) -> None:
    kube, seen = client(tmp_path, lambda r: httpx.Response(200, json={}))
    await kube.patch_secret("j", {"stringData": {"token": "new"}})
    assert seen[0].method == "PATCH"
    assert seen[0].url.path == "/api/v1/namespaces/jarvis-live-lab/secrets/j"
    assert seen[0].headers["Content-Type"] == "application/merge-patch+json"


async def test_a_missing_job_reads_as_none_and_deletes_cascade(tmp_path: Path) -> None:
    kube, seen = client(tmp_path, lambda r: httpx.Response(404, json={"message": "nope"}))
    assert await kube.get_job("j") is None
    await kube.delete_job("j")  # already gone: fine
    assert seen[1].method == "DELETE"
    assert json.loads(seen[1].content) == {"propagationPolicy": "Background"}


async def test_lists_the_jobs_pods_by_label(tmp_path: Path) -> None:
    kube, seen = client(tmp_path, lambda r: httpx.Response(200, json={"items": [{"x": 1}]}))
    assert await kube.list_pods("job-name=j") == [{"x": 1}]
    assert seen[0].url.path == "/api/v1/namespaces/jarvis-live-lab/pods"
    assert seen[0].url.params["labelSelector"] == "job-name=j"


async def test_errors_carry_the_status_and_message(tmp_path: Path) -> None:
    kube, _ = client(tmp_path, lambda r: httpx.Response(403, json={"message": "forbidden: jobs"}))
    with pytest.raises(KubeError, match="403.*forbidden: jobs"):
        await kube.create_job({})


def job(**status: Any) -> dict[str, Any]:
    return {"metadata": {"name": "j"}, "status": status}


def pod(
    push: dict[str, Any] | None = None, init: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "status": {
            "initContainerStatuses": init or [],
            "containerStatuses": [{"name": "push", "state": push}] if push else [],
        }
    }


def terminated(code: int, message: str = "", reason: str = "Completed") -> dict[str, Any]:
    return {"terminated": {"exitCode": code, "message": message, "reason": reason}}


def test_a_running_job_is_not_done() -> None:
    assert interpret(job(active=1), [pod(push={"running": {}})]) == RunState(done=False)


def test_the_push_steps_report_is_the_outcome() -> None:
    report = {
        "outcome": "pr_opened",
        "pr_url": "https://github.com/o/r/pull/7",
        "pr_number": 7,
        "branch": "claude/work-x",
        "summary": "Added retries.",
    }
    state = interpret(job(succeeded=1), [pod(push=terminated(0, json.dumps(report)))])
    assert state == RunState(
        done=True,
        outcome="pr_opened",
        pr_url="https://github.com/o/r/pull/7",
        pr_number=7,
        branch="claude/work-x",
        summary="Added retries.",
    )


def test_a_deadline_is_a_timeout() -> None:
    failed = job(
        failed=1, conditions=[{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}]
    )
    assert interpret(failed, [pod()]) == RunState(
        done=True, outcome="timed_out", error="the run hit its deadline"
    )


def test_a_failed_step_names_itself() -> None:
    failed = job(
        failed=1,
        conditions=[{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
    )
    init = [
        {"name": "prepare", "state": terminated(0)},
        {"name": "claude", "state": terminated(2, "boom", reason="Error")},
    ]
    state = interpret(failed, [pod(init=init)])
    assert state.done and state.outcome == "failed"
    assert state.error == "claude exited 2 (Error): boom"


def test_a_missing_job_failed() -> None:
    assert interpret(None, []) == RunState(done=True, outcome="failed", error="the Job is gone")


def test_an_unreadable_report_is_a_failure() -> None:
    state = interpret(job(succeeded=1), [pod(push=terminated(0, "not json"))])
    assert state.outcome == "failed" and "report" in (state.error or "")
