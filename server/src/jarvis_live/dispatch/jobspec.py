"""The Kubernetes objects one dispatch runs as: a Secret and a Job.

The pod runs the runner image's three steps in order, as init containers then the main one:

  prepare  (GitHub token)  fetch the repo's mirror, create this item's git worktree
  claude   (Claude token)  headless Claude Code in the worktree; writes a patch to /out
  push     (GitHub token)  apply the patch to a fresh clone, push a branch, open a draft PR

Each credential is mounted only into the step that needs it, so Claude never holds a GitHub
token. The mirror is writable only by ``prepare``; ``push`` never sees Claude's worktree (it
reads only the patch), so nothing Claude plants there runs with a credential. A Postgres
sidecar gives repo tests a database. No Kubernetes credentials reach the pod, and a
NetworkPolicy (server/deploy) keeps it off the cluster and the LAN."""

import json
import uuid
from dataclasses import dataclass
from typing import Any

APP = "jarvis-dispatch"
LABEL = "jarvis.apj.dev/dispatch"
UID = 1000
POSTGRES_UID = 70  # the alpine image's postgres user


@dataclass(frozen=True)
class RunSpec:
    dispatch_id: uuid.UUID
    repo: str
    branch: str
    title: str
    item_url: str
    image: str
    model: str
    deadline_s: int
    oauth_secret: str
    mirror_pvc: str
    postgres_image: str
    node_selector: dict[str, str]

    @property
    def job_name(self) -> str:
        return f"{APP}-{self.dispatch_id.hex[:8]}"

    def labels(self) -> dict[str, str]:
        return {"app": APP, LABEL: str(self.dispatch_id)}


def _locked(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        **(extra or {}),
    }


def _resources(cpu: str, memory: str, cpu_max: str, memory_max: str) -> dict[str, Any]:
    return {
        "requests": {"cpu": cpu, "memory": memory},
        "limits": {"cpu": cpu_max, "memory": memory_max},
    }


def _env(**values: str) -> list[dict[str, Any]]:
    return [{"name": k, "value": v} for k, v in values.items()]


def _secret_volume(name: str, secret: str, keys: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "secret": {
            "secretName": secret,
            "items": [{"key": k, "path": k} for k in keys],
            "defaultMode": 0o400,
        },
    }


def job_manifest(spec: RunSpec) -> dict[str, Any]:
    name = spec.job_name
    step_env = _env(REPO=spec.repo, BRANCH=spec.branch, MIRROR="/mirror", WORK="/work")
    prepare = {
        "name": "prepare",
        "image": spec.image,
        "args": ["prepare"],
        "env": step_env,
        "volumeMounts": [
            {"name": "github", "mountPath": "/secrets/github", "readOnly": True},
            {"name": "mirror", "mountPath": "/mirror"},
            {"name": "work", "mountPath": "/work"},
        ],
        "resources": _resources("100m", "256Mi", "1", "1Gi"),
        "securityContext": _locked(),
    }
    postgres = {
        "name": "postgres",
        "image": spec.postgres_image,
        "restartPolicy": "Always",  # a native sidecar: up for the claude step's tests
        "env": _env(
            POSTGRES_HOST_AUTH_METHOD="trust",
            PGDATA="/var/lib/postgresql/data/pgdata",
        ),
        "volumeMounts": [
            {"name": "pgdata", "mountPath": "/var/lib/postgresql/data"},
            {"name": "pgrun", "mountPath": "/var/run/postgresql"},
        ],
        "resources": _resources("100m", "256Mi", "1", "1Gi"),
        "securityContext": _locked({"runAsUser": POSTGRES_UID, "runAsGroup": POSTGRES_UID}),
    }
    claude = {
        "name": "claude",
        "image": spec.image,
        "args": ["claude"],
        "env": [
            *step_env,
            *_env(
                MODEL=spec.model,
                HOME="/home/runner",
                CI="true",
                DISABLE_AUTOUPDATER="1",
                JARVIS_LIVE_TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:5432/postgres",
            ),
            {
                "name": "CLAUDE_CODE_OAUTH_TOKEN",
                "valueFrom": {"secretKeyRef": {"name": spec.oauth_secret, "key": "password"}},
            },
        ],
        "volumeMounts": [
            {"name": "task", "mountPath": "/task", "readOnly": True},
            {"name": "mirror", "mountPath": "/mirror", "readOnly": True},
            {"name": "work", "mountPath": "/work"},
            {"name": "out", "mountPath": "/out"},
            {"name": "home", "mountPath": "/home/runner"},
        ],
        "resources": _resources("500m", "1Gi", "2", "6Gi"),
        "securityContext": _locked(),
    }
    push = {
        "name": "push",
        "image": spec.image,
        "args": ["push"],
        "env": step_env,
        "volumeMounts": [
            {"name": "github", "mountPath": "/secrets/github", "readOnly": True},
            {"name": "meta", "mountPath": "/task", "readOnly": True},
            {"name": "mirror", "mountPath": "/mirror", "readOnly": True},
            {"name": "out", "mountPath": "/out", "readOnly": True},
            {"name": "scratch", "mountPath": "/scratch"},
        ],
        "resources": _resources("100m", "256Mi", "1", "1Gi"),
        "securityContext": _locked(),
        "terminationMessagePolicy": "File",
    }
    pod = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "nodeSelector": dict(spec.node_selector),
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": UID,
            "runAsGroup": UID,
            "fsGroup": UID,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "initContainers": [prepare, postgres, claude],
        "containers": [push],
        "volumes": [
            _secret_volume("github", name, ["token"]),
            _secret_volume("task", name, ["prompt.md", "meta.json"]),
            _secret_volume("meta", name, ["prompt.md", "meta.json"]),
            {"name": "mirror", "persistentVolumeClaim": {"claimName": spec.mirror_pvc}},
            {"name": "work", "emptyDir": {"sizeLimit": "8Gi"}},
            {"name": "out", "emptyDir": {"sizeLimit": "256Mi"}},
            {"name": "home", "emptyDir": {"sizeLimit": "4Gi"}},
            {"name": "scratch", "emptyDir": {"sizeLimit": "8Gi"}},
            {"name": "pgdata", "emptyDir": {"sizeLimit": "2Gi"}},
            {"name": "pgrun", "emptyDir": {"medium": "Memory", "sizeLimit": "16Mi"}},
        ],
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": spec.labels()},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": spec.deadline_s,
            "ttlSecondsAfterFinished": 86_400,
            "template": {"metadata": {"labels": spec.labels()}, "spec": pod},
        },
    }


def secret_manifest(spec: RunSpec, *, token: str, prompt: str) -> dict[str, Any]:
    meta = {
        "repo": spec.repo,
        "branch": spec.branch,
        "title": spec.title,
        "item_url": spec.item_url,
        "dispatch_id": str(spec.dispatch_id),
    }
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": spec.job_name, "labels": spec.labels()},
        "stringData": {"token": token, "prompt.md": prompt, "meta.json": json.dumps(meta)},
    }
