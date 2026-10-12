"""An in-memory Kubernetes for dispatch tests: records objects, lets a test move a Job on."""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from jarvis_live.dispatch.kube import KubeError


@dataclass
class FakeKube:
    namespace: str = "jarvis-live-lab"
    secrets: dict[str, dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    pods: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    patches: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    deleted_jobs: list[str] = field(default_factory=list)
    deleted_secrets: list[str] = field(default_factory=list)
    fail_job_create: bool = False

    async def create_secret(self, manifest: dict[str, Any]) -> dict[str, Any]:
        self.secrets[manifest["metadata"]["name"]] = manifest
        return manifest

    async def create_job(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if self.fail_job_create:
            raise KubeError(403, "jobs.batch is forbidden")
        name = manifest["metadata"]["name"]
        manifest = {**manifest, "metadata": {**manifest["metadata"], "uid": str(uuid.uuid4())}}
        manifest["status"] = {"active": 1}
        self.jobs[name] = manifest
        return manifest

    async def patch_secret(self, name: str, patch: dict[str, Any]) -> None:
        self.patches.append((name, patch))

    async def get_job(self, name: str) -> dict[str, Any] | None:
        return self.jobs.get(name)

    async def list_pods(self, selector: str) -> list[dict[str, Any]]:
        return self.pods.get(selector.removeprefix("job-name="), [])

    async def delete_job(self, name: str) -> None:
        self.deleted_jobs.append(name)
        self.jobs.pop(name, None)

    async def delete_secret(self, name: str) -> None:
        self.deleted_secrets.append(name)
        self.secrets.pop(name, None)

    # --- test controls --------------------------------------------------------------------

    def report(self, name: str, **report: Any) -> None:
        """The run finished and the push step reported ``report``."""
        self.jobs[name]["status"] = {"succeeded": 1}
        message = json.dumps(report)
        self.pods[name] = [
            {
                "status": {
                    "containerStatuses": [
                        {
                            "name": "push",
                            "state": {"terminated": {"exitCode": 0, "message": message}},
                        }
                    ]
                }
            }
        ]

    def deadline(self, name: str) -> None:
        self.jobs[name]["status"] = {
            "failed": 1,
            "conditions": [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}],
        }


@dataclass
class FakeTokens:
    minted: list[str] = field(default_factory=list)
    error: Exception | None = None

    async def mint(self, repo: str) -> str:
        if self.error is not None:
            raise self.error
        self.minted.append(repo)
        return f"ghs_{len(self.minted)}"
