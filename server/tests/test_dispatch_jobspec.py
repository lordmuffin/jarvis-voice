"""The Job a dispatch runs as: which container holds which credential, and the fences."""

import json
import uuid
from typing import Any

from jarvis_live.dispatch.jobspec import RunSpec, job_manifest, secret_manifest

DID = uuid.UUID("12345678-0000-4000-8000-000000000001")
SPEC = RunSpec(
    dispatch_id=DID,
    repo="lordmuffin/jarvis-voice",
    branch="claude/work-3f2a9c-w1-retry",
    title="Retry failed Gotify notifications",
    item_url="https://live.example/sessions/s#work-items",
    image="ghcr.io/lordmuffin/jarvis-dispatch-runner:sha-0123456",
    model="claude-sonnet-5-5",
    deadline_s=3600,
    oauth_secret="claude-oauth",
    mirror_pvc="jarvis-dispatch-mirrors",
    postgres_image="postgres:16.4-alpine",
    node_selector={"kubernetes.io/arch": "amd64"},
)


def pod() -> dict[str, Any]:
    spec: dict[str, Any] = job_manifest(SPEC)["spec"]["template"]["spec"]
    return spec


def containers() -> dict[str, dict[str, Any]]:
    p = pod()
    return {c["name"]: c for c in [*p["initContainers"], *p["containers"]]}


def mounts(name: str) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in containers()[name].get("volumeMounts", [])}


def test_names_and_labels_come_from_the_dispatch() -> None:
    job = job_manifest(SPEC)
    assert SPEC.job_name == "jarvis-dispatch-12345678"
    assert job["metadata"]["name"] == SPEC.job_name
    assert job["metadata"]["labels"]["app"] == "jarvis-dispatch"
    assert job["metadata"]["labels"]["jarvis.apj.dev/dispatch"] == str(DID)
    assert job["spec"]["template"]["metadata"]["labels"]["app"] == "jarvis-dispatch"


def test_one_try_bounded_in_time_and_cleaned_up() -> None:
    spec = job_manifest(SPEC)["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 3600
    assert spec["ttlSecondsAfterFinished"] == 86_400
    assert pod()["restartPolicy"] == "Never"


def test_no_kubernetes_credentials_in_the_pod() -> None:
    assert pod()["automountServiceAccountToken"] is False
    assert pod()["enableServiceLinks"] is False


def test_steps_run_in_order_prepare_postgres_claude_then_push() -> None:
    p = pod()
    assert [c["name"] for c in p["initContainers"]] == ["prepare", "postgres", "claude"]
    assert p["initContainers"][1]["restartPolicy"] == "Always"  # a native sidecar
    assert [c["name"] for c in p["containers"]] == ["push"]
    for name in ("prepare", "claude", "push"):
        assert containers()[name]["args"] == [name]


def test_the_github_token_reaches_only_prepare_and_push() -> None:
    assert "github" in mounts("prepare") and "github" in mounts("push")
    assert "github" not in mounts("claude") and "github" not in mounts("postgres")
    vol = {v["name"]: v for v in pod()["volumes"]}["github"]
    assert vol["secret"] == {
        "secretName": SPEC.job_name,
        "items": [{"key": "token", "path": "token"}],
        "defaultMode": 0o400,
    }


def test_the_claude_token_and_prompt_reach_only_the_claude_step() -> None:
    def oauth(name: str) -> bool:
        return "claude-oauth" in json.dumps(containers()[name])

    assert oauth("claude")
    assert not any(oauth(n) for n in ("prepare", "postgres", "push"))
    env = {e["name"]: e for e in containers()["claude"]["env"]}
    assert env["CLAUDE_CODE_OAUTH_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "claude-oauth",
        "key": "password",
    }
    assert env["MODEL"]["value"] == "claude-sonnet-5-5"
    assert "task" in mounts("claude") and "task" not in mounts("push")


def test_the_mirror_is_read_only_except_to_prepare_and_push_never_sees_the_worktree() -> None:
    assert mounts("prepare")["mirror"].get("readOnly") is not True
    assert mounts("claude")["mirror"]["readOnly"] is True
    assert mounts("push")["mirror"]["readOnly"] is True
    assert "work" in mounts("claude") and "work" not in mounts("push")
    assert mounts("push")["out"]["readOnly"] is True


def test_every_container_is_locked_down_and_sized() -> None:
    for name, c in containers().items():
        sc = c["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False, name
        assert sc["capabilities"] == {"drop": ["ALL"]}, name
        assert c["resources"]["requests"] and c["resources"]["limits"], name
    assert pod()["securityContext"]["runAsNonRoot"] is True
    assert pod()["nodeSelector"] == {"kubernetes.io/arch": "amd64"}


def test_the_secret_carries_token_prompt_and_metadata() -> None:
    sec = secret_manifest(SPEC, token="ghs_abc", prompt="# Task")
    assert sec["metadata"]["name"] == SPEC.job_name
    assert sec["type"] == "Opaque"
    data = sec["stringData"]
    assert data["token"] == "ghs_abc" and data["prompt.md"] == "# Task"
    assert json.loads(data["meta.json"]) == {
        "repo": "lordmuffin/jarvis-voice",
        "branch": "claude/work-3f2a9c-w1-retry",
        "title": "Retry failed Gotify notifications",
        "item_url": "https://live.example/sessions/s#work-items",
        "dispatch_id": str(DID),
    }
