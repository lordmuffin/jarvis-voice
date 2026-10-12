"""Kubernetes REST access for dispatch: a few calls on Jobs, Secrets and Pods in one
namespace, with the pod's own service account. No client library: httpx, like publish/github.py.

``interpret`` turns a Job and its pod into a ``RunState``. The push step reports its outcome
as its termination message (JSON, at most 4 KiB), so the result reaches the server through the
Kubernetes API and the fenced pod needs no route back to it."""

import json
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
TIMEOUT_S = 30.0


class KubeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Kubernetes {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class RunState:
    done: bool
    outcome: str | None = None  # pr_opened | no_changes | failed | rate_limited | timed_out
    pr_url: str | None = None
    pr_number: int | None = None
    branch: str | None = None
    summary: str | None = None
    error: str | None = None


class KubeClient:
    def __init__(
        self, client: httpx.AsyncClient, *, api: str, token_file: Path, namespace: str
    ) -> None:
        self._client = client
        self._api = api.rstrip("/")
        self._token_file = token_file
        self.namespace = namespace

    @classmethod
    def in_cluster(cls, namespace: str = "") -> "KubeClient":
        """The pod's service account: its token (re-read on every call, as it rotates), CA
        and namespace."""
        ctx = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        ns = namespace or (SA_DIR / "namespace").read_text().strip()
        return cls(
            httpx.AsyncClient(verify=ctx, timeout=TIMEOUT_S),
            api=f"https://{host}:{port}",
            token_file=SA_DIR / "token",
            namespace=ns,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _path(self, group: str, kind: str, name: str = "") -> str:
        base = "/api/v1" if group == "" else f"/apis/{group}"
        return f"{base}/namespaces/{self.namespace}/{kind}" + (f"/{name}" if name else "")

    async def _call(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        content_type: str = "application/json",
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        token = self._token_file.read_text().strip()
        r = await self._client.request(
            method,
            self._api + path,
            content=json.dumps(body) if body is not None else None,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
            timeout=TIMEOUT_S,
        )
        if r.status_code == 404 and missing_ok:
            return None
        if r.status_code >= 400:
            try:
                message = str(r.json().get("message") or r.text)
            except ValueError:
                message = r.text
            raise KubeError(r.status_code, message[:500])
        data: dict[str, Any] = r.json() if r.content else {}
        return data

    async def create_job(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", self._path("batch/v1", "jobs"), body=manifest) or {}

    async def create_secret(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", self._path("", "secrets"), body=manifest) or {}

    async def patch_secret(self, name: str, patch: dict[str, Any]) -> None:
        await self._call(
            "PATCH",
            self._path("", "secrets", name),
            body=patch,
            content_type="application/merge-patch+json",
        )

    async def get_job(self, name: str) -> dict[str, Any] | None:
        return await self._call("GET", self._path("batch/v1", "jobs", name), missing_ok=True)

    async def delete_job(self, name: str) -> None:
        await self._call(
            "DELETE",
            self._path("batch/v1", "jobs", name),
            body={"propagationPolicy": "Background"},
            missing_ok=True,
        )

    async def list_pods(self, selector: str) -> list[dict[str, Any]]:
        data = await self._call("GET", self._path("", "pods"), params={"labelSelector": selector})
        return list((data or {}).get("items", []))


def _condition(job: dict[str, Any], kind: str) -> dict[str, Any] | None:
    for c in job.get("status", {}).get("conditions", []) or []:
        if c.get("type") == kind and c.get("status") == "True":
            return dict(c)
    return None


def _statuses(pods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in pods:
        st = p.get("status", {})
        out += st.get("initContainerStatuses", []) or []
        out += st.get("containerStatuses", []) or []
    return out


def _report(statuses: list[dict[str, Any]]) -> RunState | None:
    for s in statuses:
        term = (s.get("state") or {}).get("terminated")
        if s.get("name") != "push" or not term:
            continue
        try:
            r = json.loads(term.get("message") or "")
        except ValueError:
            return RunState(
                done=True, outcome="failed", error="the push step's report is unreadable"
            )
        if not isinstance(r, dict) or not r.get("outcome"):
            return RunState(
                done=True, outcome="failed", error="the push step's report is unreadable"
            )
        number = r.get("pr_number")
        return RunState(
            done=True,
            outcome=str(r["outcome"]),
            pr_url=r.get("pr_url"),
            pr_number=int(number) if number is not None else None,
            branch=r.get("branch"),
            summary=r.get("summary"),
            error=r.get("error"),
        )
    return None


def interpret(job: dict[str, Any] | None, pods: list[dict[str, Any]]) -> RunState:
    if job is None:
        return RunState(done=True, outcome="failed", error="the Job is gone")
    statuses = _statuses(pods)
    failed = _condition(job, "Failed")
    if failed and failed.get("reason") == "DeadlineExceeded":
        return RunState(done=True, outcome="timed_out", error="the run hit its deadline")
    if (report := _report(statuses)) is not None:
        return report
    if failed or job.get("status", {}).get("failed"):
        for s in statuses:
            term = (s.get("state") or {}).get("terminated")
            if term and term.get("exitCode"):
                msg = (term.get("message") or "").strip()[:500]
                error = f"{s['name']} exited {term['exitCode']} ({term.get('reason', '')})"
                return RunState(
                    done=True, outcome="failed", error=error + (f": {msg}" if msg else "")
                )
        return RunState(
            done=True, outcome="failed", error=str((failed or {}).get("reason") or "failed")
        )
    if job.get("status", {}).get("succeeded"):
        return RunState(done=True, outcome="failed", error="the push step left no report")
    return RunState(done=False)
